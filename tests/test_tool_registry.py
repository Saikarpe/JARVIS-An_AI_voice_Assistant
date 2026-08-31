"""
Backend/tools/registry.py — the @tool decorator's schema generation from
type hints, and call_tool()'s dispatch/error handling (Phase 6, see
ENHANCEMENT_PLAN.md's test priority list, item 1).

Every test here uses the clean_registry fixture (tests/conftest.py) so
none of it touches the real, process-wide tool registry that Backend.agent
populates via `import Backend.tools`.
"""

import pytest


def test_schema_shape_and_required_vs_optional(clean_registry):
    @clean_registry.tool("adds two numbers")
    def add(a: int, b: int = 0) -> str:
        return str(a + b)

    schemas = clean_registry.get_schemas()
    assert len(schemas) == 1

    schema = schemas[0]
    assert schema["type"] == "function"
    fn = schema["function"]
    assert fn["name"] == "add"
    assert fn["description"] == "adds two numbers"

    props = fn["parameters"]["properties"]
    assert props["a"]["type"] == "integer"
    assert props["b"]["type"] == "integer"
    # a has no default -> required; b has one -> not required.
    assert fn["parameters"]["required"] == ["a"]


@pytest.mark.parametrize(
    "py_type,json_type",
    [(str, "string"), (int, "integer"), (float, "number"), (bool, "boolean")],
)
def test_every_supported_type_maps_correctly(clean_registry, py_type, json_type):
    def make_tool():
        # Built dynamically since the annotation itself is the thing under test.
        ns = {}
        exec(
            f"def fn(x: {py_type.__name__}) -> str:\n    return str(x)",
            {py_type.__name__: py_type}, ns,
        )
        return ns["fn"]

    clean_registry.tool("single-arg tool")(make_tool())
    schema = clean_registry.get_schemas()[0]["function"]
    assert schema["parameters"]["properties"]["x"]["type"] == json_type


def test_unhinted_parameter_defaults_to_string(clean_registry):
    @clean_registry.tool("greets someone")
    def greet(name) -> str:  # no type hint at all
        return f"hello {name}"

    schema = clean_registry.get_schemas()[0]["function"]
    assert schema["parameters"]["properties"]["name"]["type"] == "string"


def test_duplicate_tool_name_raises(clean_registry):
    @clean_registry.tool("first registration")
    def dup() -> str:
        return "a"

    with pytest.raises(ValueError, match="already registered"):
        @clean_registry.tool("second registration, same name")
        def dup() -> str:  # noqa: F811 — the redefinition is the point of the test
            return "b"


def test_call_tool_dispatches_and_stringifies_result(clean_registry):
    @clean_registry.tool("adds two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    result = clean_registry.call_tool("add", {"a": 2, "b": 3})
    assert result == "5"  # call_tool always returns str(), even for an int return value


def test_call_tool_unknown_name_reports_available_tools(clean_registry):
    @clean_registry.tool("adds two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    result = clean_registry.call_tool("subtract", {})
    assert result.startswith("Error: unknown tool 'subtract'")
    assert "add" in result  # the "available tools" list should mention what IS registered


def test_call_tool_missing_required_argument_is_a_soft_error(clean_registry):
    @clean_registry.tool("adds two numbers")
    def add(a: int, b: int) -> int:
        return a + b

    result = clean_registry.call_tool("add", {"a": 1})  # missing b
    assert result.startswith("Error: bad arguments for 'add'")


def test_call_tool_never_raises_out_of_the_function_body(clean_registry):
    """The whole point of call_tool()'s try/except: a tool's own exception
    must come back as a string the agent loop can feed to the model, never
    propagate and kill the loop (ENHANCEMENT_PLAN.md Phase 2's registry.py
    reference implementation docstring makes this the explicit contract)."""

    @clean_registry.tool("always fails")
    def boom() -> str:
        raise RuntimeError("kaboom")

    result = clean_registry.call_tool("boom", {})
    assert result.startswith("Error running 'boom'")
    assert "kaboom" in result


def test_get_tool_names_matches_get_schemas(clean_registry):
    @clean_registry.tool("a")
    def tool_a() -> str:
        return "a"

    @clean_registry.tool("b")
    def tool_b() -> str:
        return "b"

    names = set(clean_registry.get_tool_names())
    schema_names = {s["function"]["name"] for s in clean_registry.get_schemas()}
    assert names == schema_names == {"tool_a", "tool_b"}


def test_real_tool_registry_is_well_formed():
    """Not using clean_registry — imports the actual Backend.tools package
    (the same `import Backend.tools` Backend/agent.py does) and checks
    every real, shipped tool's generated schema is structurally valid:
    unique names, non-empty descriptions, and only JSON-schema types the
    @tool decorator is documented to produce."""
    import Backend.tools  # noqa: F401 — side effect: registers every real tool
    from Backend.tools.registry import get_schemas

    schemas = get_schemas()
    assert len(schemas) > 0

    seen_names = set()
    for schema in schemas:
        assert schema["type"] == "function"
        fn = schema["function"]
        assert fn["name"] not in seen_names, f"duplicate tool name: {fn['name']}"
        seen_names.add(fn["name"])
        assert fn["description"].strip(), f"{fn['name']} has an empty description"
        for param_name, prop in fn["parameters"]["properties"].items():
            assert prop["type"] in ("string", "integer", "number", "boolean"), (
                f"{fn['name']}.{param_name} has an unexpected JSON type: {prop['type']}"
            )
