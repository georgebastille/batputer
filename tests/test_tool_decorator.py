from tools.commons import TOOL_CALLABLES, TOOLS_REGISTRY, tool


def test_tool_schema_basic():
    @tool
    def add(a: int, b: int) -> int:
        """Add two numbers.

        Args:
            a: First operand.
            b: Second operand.
        """
        return a + b

    schema = add._tool_schema
    fn_schema = schema["function"]
    assert schema["type"] == "function"
    assert fn_schema["name"] == "add"
    assert fn_schema["description"] == "Add two numbers."
    assert set(fn_schema["parameters"]["required"]) == {"a", "b"}
    assert fn_schema["parameters"]["properties"]["a"]["type"] == "integer"
    assert fn_schema["parameters"]["properties"]["b"]["type"] == "integer"
    assert fn_schema["parameters"]["properties"]["a"]["description"] == "First operand."
    # Decorator is transparent — function still works
    assert add(2, 3) == 5
    assert "add" in TOOL_CALLABLES
    assert any(s["function"]["name"] == "add" for s in TOOLS_REGISTRY)


def test_tool_schema_optional_param():
    @tool
    def search(query: str, max_results: int = 5) -> str:
        """Search something.

        Args:
            query: The search query.
            max_results: How many results.
        """
        return query

    fn_schema = search._tool_schema["function"]
    assert fn_schema["parameters"]["required"] == ["query"]
    assert "max_results" in fn_schema["parameters"]["properties"]
    assert fn_schema["parameters"]["properties"]["max_results"]["type"] == "integer"
