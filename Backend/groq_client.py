"""
Shared, lazily-constructed Groq client (Phase 6, see ENHANCEMENT_PLAN.md).

Backend/agent.py, Backend/tools/files.py, and Backend/SpeechToText.py each
used to build their own `Groq(api_key=env_vars.get("GroqAPIKey"))` at
*import* time. The Groq SDK raises immediately if api_key is falsy — so
simply importing any of those modules with no .env (a fresh checkout
before `cp .env.example .env`, or a CI runner, which should never have
real credentials) crashed before a single line of application code ran,
let alone a test. Building the client on first *use* instead means import
stays cheap and side-effect-free; the error only surfaces if something
actually tries to call the API without a key configured, which is the
right place for it to surface.
"""

from dotenv import dotenv_values
from groq import Groq

_client = None


def get_groq_client() -> Groq:
    global _client
    if _client is None:
        env_vars = dotenv_values(".env")
        _client = Groq(api_key=env_vars.get("GroqAPIKey"))
    return _client
