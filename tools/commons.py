import inspect
import re
import typing

TOOLS_REGISTRY: list[dict] = []
TOOL_CALLABLES: dict[str, callable] = {}

_TYPE_MAP = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _py_type_to_json_schema(annotation) -> dict:
    if annotation is inspect.Parameter.empty:
        return {}
    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)
    if origin is typing.Union:
        non_none = [a for a in args if a is not type(None)]
        if len(non_none) == 1:
            return _py_type_to_json_schema(non_none[0])
        return {}
    if origin is list:
        schema = {"type": "array"}
        if args:
            schema["items"] = _py_type_to_json_schema(args[0])
        return schema
    return {"type": _TYPE_MAP[annotation]} if annotation in _TYPE_MAP else {}


def _parse_google_docstring(doc: str) -> tuple[str, dict[str, str]]:
    if not doc:
        return "", {}
    lines = doc.splitlines()
    summary_lines, param_docs = [], {}
    in_args = False
    last_param = None
    for line in lines:
        stripped = line.strip()
        if stripped in ("Args:", "Arguments:"):
            in_args = True
            continue
        if stripped in ("Returns:", "Raises:", "Example:", "Examples:", "Note:", "Notes:"):
            in_args = False
            last_param = None
            continue
        if not in_args:
            summary_lines.append(stripped)
        else:
            m = re.match(r"^\s{4,8}(\w+):\s+(.+)", line)
            if m:
                last_param = m.group(1)
                param_docs[last_param] = m.group(2).strip()
            elif last_param and re.match(r"^\s{8,}", line) and stripped:
                param_docs[last_param] += " " + stripped
    summary = " ".join(l for l in summary_lines if l)
    return summary, param_docs


def _build_schema(fn) -> dict:
    sig = inspect.signature(fn)
    hints = typing.get_type_hints(fn)
    summary, param_docs = _parse_google_docstring(inspect.getdoc(fn))
    properties = {}
    required = []
    for name, param in sig.parameters.items():
        annotation = hints.get(name, inspect.Parameter.empty)
        prop = _py_type_to_json_schema(annotation)
        if name in param_docs:
            prop["description"] = param_docs[name]
        properties[name] = prop
        if param.default is inspect.Parameter.empty:
            required.append(name)
    return {
        "type": "function",
        "function": {
            "name": fn.__name__,
            "description": summary,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        },
    }


def tool(fn):
    schema = _build_schema(fn)
    TOOLS_REGISTRY.append(schema)
    TOOL_CALLABLES[fn.__name__] = fn
    fn._tool_schema = schema
    return fn
