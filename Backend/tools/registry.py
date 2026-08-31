"""
Tool registry — the mechanism the agent loop (Backend/agent.py) uses to
discover and call the functions Jarvis can invoke.

This replaces Backend/Model.py's old approach: a single Cohere prompt asking
for a comma-separated string like "open chrome, general who is akbar",
parsed with str.startswith() against a hardcoded Functions list in main.py.
That was single-step (no "search, then act on what you found"), had no
typed arguments, and broke on a comma inside the user's own query.

Instead, every tool is a plain Python function decorated with @tool(...).
The JSON schema Groq's function-calling API needs is derived straight from
the function's signature and type hints, so the schema can never drift out
of sync with the implementation — there's exactly one source of truth.
"""

import inspect
from typing import Any, Callable, Dict, get_type_hints

_TOOLS: Dict[str, Dict[str, Any]] = {}

_PY_TO_JSON_TYPE = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
}


def tool(description: str):
    """Register a function as an agent tool.

    Usage:
        @tool("Search the web for current information.")
        def web_search(query: str, num_results: int = 5) -> str:
            ...

    Every parameter must have a type hint (str, int, float, or bool); a
    parameter with no default is marked required in the generated schema.
    """
    def decorator(fn: Callable) -> Callable:
        hints = get_type_hints(fn)
        sig = inspect.signature(fn)
        props: Dict[str, Any] = {}
        required = []
        for name, param in sig.parameters.items():
            py_type = hints.get(name, str)
            props[name] = {
                "type": _PY_TO_JSON_TYPE.get(py_type, "string"),
                "description": f"{name} parameter",
            }
            if param.default is inspect.Parameter.empty:
                required.append(name)

        if fn.__name__ in _TOOLS:
            raise ValueError(f"Tool '{fn.__name__}' is already registered — tool names must be unique")

        _TOOLS[fn.__name__] = {
            "fn": fn,
            "schema": {
                "type": "function",
                "function": {
                    "name": fn.__name__,
                    "description": description,
                    "parameters": {
                        "type": "object",
                        "properties": props,
                        "required": required,
                    },
                },
            },
        }
        return fn
    return decorator


def get_schemas():
    """Return every registered tool's JSON schema, ready to pass as `tools=`
    to a Groq/OpenAI-compatible chat completions call."""
    return [t["schema"] for t in _TOOLS.values()]


def get_tool_names():
    return list(_TOOLS.keys())


def call_tool(name: str, arguments: dict) -> str:
    """Invoke a registered tool by name. Never raises — any failure (bad
    arguments, the tool's own exception) comes back as a string starting
    with "Error", which gets fed back to the model as the tool result so it
    can retry, use a different tool, or explain the failure to the user,
    rather than crashing the whole agent loop."""
    if name not in _TOOLS:
        return f"Error: unknown tool '{name}'. Available tools: {', '.join(get_tool_names())}"
    try:
        return str(_TOOLS[name]["fn"](**arguments))
    except TypeError as e:
        return f"Error: bad arguments for '{name}': {e}"
    except Exception as e:
        return f"Error running '{name}': {e}"
