"""Content-writing tool: ports Backend/Automation.py's Content()/ContentWriterAI()
logic — a separate small Groq call that drafts the actual text (letter, essay,
code, email, ...), then saves it to Data/ and opens it in Notepad."""

import os
import re
import subprocess

from Backend.config import GROQ_CHAT_MODEL
from Backend.groq_client import get_groq_client
from Backend.tools.registry import tool

_SYSTEM = (
    "You are a professional content writer. Write the requested content "
    "(letter, code, application, essay, email, etc.) in a well-structured, "
    "clear way. Output only the content itself — no preamble like 'Here is "
    "your letter:' and no markdown code fences."
)


def _safe_filename(topic: str) -> str:
    name = re.sub(r"[^a-z0-9]+", "", topic.lower())
    return name[:60] or "content"


@tool(
    "Write content (a letter, essay, code, email, application, etc.) about a "
    "topic, save it to a file, and open it in Notepad for the user to see."
)
def write_document(topic: str) -> str:
    try:
        completion = get_groq_client().chat.completions.create(
            model=GROQ_CHAT_MODEL,
            messages=[
                {"role": "system", "content": _SYSTEM},
                {"role": "user", "content": topic},
            ],
            max_tokens=2048,
            temperature=0.7,
            top_p=1,
            stream=True,
            timeout=30,
        )
        answer = ""
        for chunk in completion:
            if chunk.choices[0].delta.content:
                answer += chunk.choices[0].delta.content
        answer = answer.replace("</s>", "").strip()
    except Exception as e:
        return f"Error generating content: {e}"

    if not answer:
        return "Error: the language model returned no content."

    os.makedirs("Data", exist_ok=True)
    file_path = os.path.join("Data", f"{_safe_filename(topic)}.txt")
    try:
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(answer)
        subprocess.Popen(["notepad.exe", file_path])
    except Exception as e:
        return f"Generated the content but failed to save/open it: {e}"

    return f"Wrote and opened {file_path} ({len(answer)} characters)."
