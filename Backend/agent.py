"""
The agentic core (Phase 2, see ENHANCEMENT_PLAN.md).

Replaces three things at once:
  - Backend/Model.py's FirstLayerDMM, which asked Cohere to classify a
    query into a comma-separated command string and could only ever take
    one step (search OR act, never "search, then act on what you found").
  - Backend/Chatbot.py's ChatBot(), for plain conversational answers.
  - Backend/RealtimeSearchEngine.py's RealtimeSearchEngine(), which always
    ran a DuckDuckGo search whether the query needed one or not.

Now there's a single Groq call with native tool-calling turned on
(tools=get_schemas(), tool_choice="auto"). The model decides per-turn
whether it needs a tool, which one, with what arguments, and whether the
result of one tool call means it should call another before answering —
real multi-step reasoning instead of a single fixed classification.

This module has no PyQt import and doesn't know AgentWorker exists — it
takes plain callback functions (on_state, on_tool_start, ...) so it stays
importable and unit-testable without a Qt event loop or an offscreen
display.
"""

import datetime
import json
import logging
import time
from typing import TypedDict

from dotenv import dotenv_values

import Backend.tools  # noqa: F401 — import side effect: registers every tool
from Backend.config import GROQ_CHAT_MODEL
from Backend.Database import get_chat_history, log_usage, recall, save_message
from Backend.groq_client import get_groq_client
from Backend.tools.registry import call_tool, get_schemas

logger = logging.getLogger(__name__)


class _ToolCallAcc(TypedDict):
    """One tool call's streamed-and-accumulated pieces (see the streaming
    loop below). id/name start None and are filled in once their chunk
    arrives; arguments starts "" and only ever has real JSON-fragment
    strings appended to it, so — unlike id/name — it's never None."""
    id: str | None
    name: str | None
    arguments: str

env_vars = dotenv_values(".env")
Username = env_vars.get("Username") or "User"
Assistantname = env_vars.get("Assistantname") or "Jarvis"

MAX_STEPS = 6  # hard ceiling; prevents a runaway tool loop or a cost blowup
TOOL_RETRY_DELAY_S = 1  # Phase 4.3 "Follow-ups": a flaky failure gets one quiet retry


def _system_prompt(user_query: str) -> str:
    now = datetime.datetime.now()
    prompt = (
        f"You are {Assistantname}, a helpful, accurate voice assistant for {Username}. "
        "Answer directly and conversationally — you're spoken aloud, so avoid markdown "
        "tables, headings, or long bullet lists; write the way you'd actually say it. "
        "Use a tool only when you actually need it: general knowledge questions, opinions, "
        "and anything you already know confidently should be answered directly with no "
        "tool call. Use web_search for anything time-sensitive or after your training "
        "cutoff. Never mention that you are an AI language model or reference your "
        "training data.\n\n"
        f"Current date/time: {now.strftime('%A, %d %B %Y, %H:%M:%S')}."
    )

    # Phase 4.1: semantic long-term memory. This is the automatic, every-turn
    # pass over what's already known about the user; recall_memory (a tool,
    # see Backend/tools/memory.py) is there for the model to run a second,
    # differently-worded search mid-conversation if this first pass — keyed
    # only on the raw incoming query — doesn't surface what it needs.
    try:
        memories = recall(user_query)
    except Exception as e:
        # A missing/failed embedding model shouldn't take down every
        # conversation turn — memory is a nice-to-have layered on top of
        # the agent loop, not a dependency of it.
        logger.warning("memory recall failed, continuing without it: %s", e)
        memories = []
    if memories:
        prompt += "\n\nThings you remember about the user:\n" + "\n".join(
            f"- {m}" for m in memories
        )

    return prompt


def _noop(*args, **kwargs):
    pass


def run_agent(
    user_query: str,
    on_state=None,
    on_tool_start=None,
    on_tool_end=None,
    on_token=None,
) -> str:
    """Run the agent loop for one user turn and return the final answer text.

    Every on_* callback is optional and called synchronously — AgentWorker
    passes its Qt signals' .emit methods so each step is visible in the UI
    the instant it happens, but nothing in here depends on Qt.
    """
    on_state = on_state or _noop
    on_tool_start = on_tool_start or _noop
    on_tool_end = on_tool_end or _noop
    on_token = on_token or _noop

    turn_start = time.time()
    tools_used: list[str] = []  # Phase 5.5 stats panel: feeds get_usage_summary()'s "by tool" breakdown

    save_message("user", user_query)
    history = get_chat_history(limit=20)
    messages = [{"role": "system", "content": _system_prompt(user_query)}] + history

    final_answer = None

    for step in range(MAX_STEPS):
        on_state("thinking")
        try:
            stream = get_groq_client().chat.completions.create(
                model=GROQ_CHAT_MODEL,
                messages=messages,
                tools=get_schemas(),
                tool_choice="auto",
                temperature=0.7,
                max_tokens=2048,
                top_p=1,
                stream=True,
                timeout=30,
            )
        except Exception as e:
            final_answer = f"Sorry, I hit an error talking to the language model: {e}"
            break

        content = ""
        tool_calls_acc: dict[int, _ToolCallAcc] = {}  # index -> accumulated tool-call pieces
        try:
            for chunk in stream:
                delta = chunk.choices[0].delta
                if delta.content:
                    content += delta.content
                    on_token(delta.content)
                if delta.tool_calls:
                    # Tool-call deltas arrive fragmented by index across
                    # chunks (id/name may come in one chunk, arguments
                    # dribble in across several more) — accumulate them.
                    for tc_delta in delta.tool_calls:
                        acc = tool_calls_acc.setdefault(
                            tc_delta.index, {"id": None, "name": None, "arguments": ""}
                        )
                        if tc_delta.id:
                            acc["id"] = tc_delta.id
                        if tc_delta.function:
                            if tc_delta.function.name:
                                acc["name"] = tc_delta.function.name
                            if tc_delta.function.arguments:
                                acc["arguments"] += tc_delta.function.arguments
        except Exception as e:
            final_answer = f"Sorry, I hit an error while streaming the response: {e}"
            break

        if not tool_calls_acc:
            final_answer = content.strip() or "I don't have a response for that."
            break

        # Record the assistant's tool-call turn, then run each tool in order.
        messages.append({
            "role": "assistant",
            "content": content or None,
            "tool_calls": [
                {
                    "id": acc["id"],
                    "type": "function",
                    "function": {"name": acc["name"], "arguments": acc["arguments"]},
                }
                for acc in tool_calls_acc.values()
            ],
        })

        on_state("tool")
        for acc in tool_calls_acc.values():
            try:
                args = json.loads(acc["arguments"]) if acc["arguments"] else {}
            except json.JSONDecodeError:
                args = {}

            # A real Groq/OpenAI-compatible stream always sends the
            # function name on a tool call's first chunk, so this is never
            # actually empty in practice — the fallback just keeps the type
            # honest (acc["name"] is str | None until then) and, if a
            # stream ever *did* arrive malformed, routes to call_tool's own
            # "unknown tool ''" error instead of crashing on None.
            tool_name = acc["name"] or ""

            tools_used.append(tool_name)
            on_tool_start(tool_name, args)
            logger.info("tool call: %s(%s)", tool_name, args)
            t0 = time.time()
            result = call_tool(tool_name, args)
            if result.startswith("Error"):
                # Phase 4.3 "Follow-ups": retry once, quietly, before this
                # failure ever reaches the model or the UI — a transient
                # search timeout or dropped connection shouldn't read as a
                # hard failure on the first try. Only a failure that
                # survives the retry is reported (call_tool's result is
                # what gets shown either way, so a fixed-by-retry call
                # looks identical to one that never failed at all).
                time.sleep(TOOL_RETRY_DELAY_S)
                retry_result = call_tool(tool_name, args)
                if not retry_result.startswith("Error"):
                    result = retry_result
            elapsed_ms = int((time.time() - t0) * 1000)
            summary = result if len(result) <= 150 else result[:150] + "..."
            on_tool_end(tool_name, f"{summary} ({elapsed_ms}ms)")
            logger.info("tool result: %s -> %s (%dms)", tool_name, summary, elapsed_ms)

            messages.append({
                "role": "tool",
                "tool_call_id": acc["id"],
                "content": result,
            })
    else:
        final_answer = "I wasn't able to finish that in a reasonable number of steps."

    if final_answer is None:
        final_answer = "I wasn't able to finish that in a reasonable number of steps."

    save_message("assistant", final_answer)

    # Phase 5.5 stats panel: get_usage_summary() was written back in the
    # original schema and imported in main.py, but nothing ever called
    # log_usage() — the table, and therefore the panel, was always empty.
    elapsed_ms = int((time.time() - turn_start) * 1000)
    is_error = final_answer.startswith("Sorry, I hit an error") or final_answer.startswith(
        "I wasn't able to finish"
    )
    query_type = ",".join(dict.fromkeys(tools_used)) if tools_used else "general"
    try:
        log_usage(
            user_query,
            query_type,
            elapsed_ms,
            status="error" if is_error else "success",
            error_message=final_answer if is_error else None,
        )
    except Exception as e:
        logger.warning("log_usage failed (non-fatal): %s", e)

    return final_answer
