"""
Backend/agent.py's run_agent() — tool dispatch and the MAX_STEPS ceiling
(Phase 6, see ENHANCEMENT_PLAN.md's test priority list, item 2).

The Groq client is fully mocked (ScriptedGroqClient below) — these tests
never make a network call. recall() is also mocked out (it would otherwise
load a real fastembed ONNX model on first use, a slow, network-touching
side effect that has nothing to do with what's under test here). Database
reads/writes (save_message, get_chat_history, log_usage) are NOT mocked —
they hit the throwaway SQLite file tests/conftest.py points JARVIS_DB_PATH
at, so this also exercises the real agent<->database integration.
"""

from types import SimpleNamespace

import pytest

import Backend.agent as agent_module
from Backend.agent import run_agent


def _content_chunk(text):
    delta = SimpleNamespace(content=text, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_call_chunk(index, id_=None, name=None, arguments=None):
    tc_delta = SimpleNamespace(
        index=index, id=id_, function=SimpleNamespace(name=name, arguments=arguments)
    )
    delta = SimpleNamespace(content=None, tool_calls=[tc_delta])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


class ScriptedGroqClient:
    """Stands in for Backend.groq_client.get_groq_client()'s return value.
    Each element of `responses` is either a list of chunks (one streamed
    completion) or an Exception instance to raise instead."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.call_count = 0

    def _create(self, **_kwargs):
        self.call_count += 1
        assert self.call_count <= len(self._responses), (
            f"agent made more Groq calls ({self.call_count}) than the test scripted "
            f"({len(self._responses)}) — likely a MAX_STEPS regression"
        )
        response = self._responses[self.call_count - 1]
        if isinstance(response, Exception):
            raise response
        return iter(response)

    @property
    def chat(self):
        return SimpleNamespace(completions=SimpleNamespace(create=self._create))


@pytest.fixture(autouse=True)
def _no_real_memory_recall(monkeypatch):
    """recall() would otherwise load a real embedding model — irrelevant
    to what these tests check and slow/network-touching on first run."""
    monkeypatch.setattr(agent_module, "recall", lambda query, **kw: [])


def _script(monkeypatch, responses):
    client = ScriptedGroqClient(responses)
    monkeypatch.setattr(agent_module, "get_groq_client", lambda: client)
    return client


def test_plain_answer_with_no_tool_call(monkeypatch):
    _script(monkeypatch, [[_content_chunk("Paris is the capital of France.")]])

    answer = run_agent("What's the capital of France?")

    assert answer == "Paris is the capital of France."


def test_tool_call_then_final_answer(monkeypatch):
    monkeypatch.setattr(agent_module, "get_schemas", lambda: [])
    calls = []

    def fake_call_tool(name, args):
        calls.append((name, args))
        return "It is sunny and 22C in Pune."

    monkeypatch.setattr(agent_module, "call_tool", fake_call_tool)

    _script(monkeypatch, [
        # Step 1: the model decides to call a tool, no content of its own yet.
        [_tool_call_chunk(0, id_="call_1", name="web_search", arguments='{"query": "weather in Pune"}')],
        # Step 2: model sees the tool result and answers.
        [_content_chunk("It's sunny and 22C in Pune right now.")],
    ])

    answer = run_agent("What's the weather in Pune?")

    assert calls == [("web_search", {"query": "weather in Pune"})]
    assert answer == "It's sunny and 22C in Pune right now."


def test_fragmented_tool_call_arguments_are_accumulated(monkeypatch):
    """Real Groq streaming responses split a tool call's JSON arguments
    across several chunks by index (ENHANCEMENT_PLAN.md's Phase 2 section
    calls this out explicitly) — this reproduces that fragmentation."""
    monkeypatch.setattr(agent_module, "get_schemas", lambda: [])
    captured_args = []

    def fake_call_tool(name, args):
        captured_args.append(args)
        return "done"

    monkeypatch.setattr(agent_module, "call_tool", fake_call_tool)

    _script(monkeypatch, [
        [
            _tool_call_chunk(0, id_="call_1", name="web_search", arguments='{"query": '),
            _tool_call_chunk(0, arguments='"weather"}'),
        ],
        [_content_chunk("Final answer.")],
    ])

    run_agent("weather?")

    assert captured_args == [{"query": "weather"}]


def test_multiple_tool_calls_in_one_step_run_in_order(monkeypatch):
    monkeypatch.setattr(agent_module, "get_schemas", lambda: [])
    calls = []
    monkeypatch.setattr(
        agent_module, "call_tool",
        lambda name, args: calls.append(name) or f"{name} result",
    )

    _script(monkeypatch, [
        [
            _tool_call_chunk(0, id_="call_1", name="tool_a", arguments="{}"),
            _tool_call_chunk(1, id_="call_2", name="tool_b", arguments="{}"),
        ],
        [_content_chunk("Combined answer.")],
    ])

    answer = run_agent("do two things")

    assert calls == ["tool_a", "tool_b"]
    assert answer == "Combined answer."


def test_max_steps_ceiling_stops_a_runaway_tool_loop(monkeypatch):
    """A model that never stops calling tools must not be allowed to loop
    forever — this is P0-1's "no unbounded recursion, ever" rule applied
    to the agent loop instead of the old ChatBot retry path."""
    monkeypatch.setattr(agent_module, "get_schemas", lambda: [])
    monkeypatch.setattr(agent_module, "call_tool", lambda name, args: "ok")

    always_tool_call = [_tool_call_chunk(0, id_="call_1", name="loop_tool", arguments="{}")]
    client = _script(monkeypatch, [always_tool_call] * agent_module.MAX_STEPS)

    answer = run_agent("keep looping")

    assert client.call_count == agent_module.MAX_STEPS
    assert "reasonable number of steps" in answer


def test_groq_api_error_is_reported_not_raised(monkeypatch):
    _script(monkeypatch, [ConnectionError("network is down")])

    answer = run_agent("hello")

    assert "error talking to the language model" in answer.lower()


def test_turn_is_logged_to_usage_stats(monkeypatch):
    from Backend.Database import _get_connection, get_usage_summary

    _get_connection().execute("DELETE FROM usage_stats")
    _get_connection().commit()

    _script(monkeypatch, [[_content_chunk("hi there")]])
    run_agent("hello")

    summary = get_usage_summary()
    assert summary["total_queries"] == 1
    assert summary["successful"] == 1
    assert summary["queries_by_type"] == {"general": 1}
