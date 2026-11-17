"""工具说明书的契约自检。

抓的是「import 能过、真发请求才崩」这一类假绿：
- 占位符 (...) 不是字符串：json.dumps 直接 TypeError
- required 里写了 properties 里不存在的参数：模型被要求传一个不存在的字段
- schema 与 registry 不同步：工具注册了但模型看不见，或反过来
"""

import json

from agent.schemas import ALL_TOOLS


def test_tools_json_serializable():
    """发给模型的整包说明书必须能序列化成 JSON。"""
    text = json.dumps(ALL_TOOLS, ensure_ascii=False)
    assert len(text) > 100


def test_required_fields_exist_in_properties():
    """required 里的每个字段都必须在 properties 里真实定义。"""
    for tool in ALL_TOOLS:
        fn = tool["function"]
        props = set(fn["parameters"]["properties"])
        for name in fn["parameters"].get("required", []):
            assert name in props, (
                f"{fn['name']}: required 里的 {name!r} 不在 properties 里"
                f"（现有 {sorted(props)}）"
            )


def test_descriptions_are_real_strings():
    """description 必须是真字符串：占位符 ... 是 Ellipsis，不是文本。"""
    for tool in ALL_TOOLS:
        fn = tool["function"]
        assert isinstance(fn["description"], str), (
            f"{fn['name']}: 顶层 description 不是字符串，实际是 {type(fn['description']).__name__}"
        )
        assert len(fn["description"]) > 10, f"{fn['name']}: 顶层 description 太短，等于没写"
        for pname, spec in fn["parameters"]["properties"].items():
            desc = spec.get("description")
            assert isinstance(desc, str) and desc, (
                f"{fn['name']}.{pname}: 缺 description 或不是字符串（实际 {desc!r}）"
            )


def test_schemas_match_registry():
    """schemas 里的名字集合必须等于 registry 里注册的集合。"""
    from agent.registry import TOOL_REGISTRY

    declared = {t["function"]["name"] for t in ALL_TOOLS}
    registered = set(TOOL_REGISTRY)
    assert declared == registered, (
        f"只在 schemas: {sorted(declared - registered)}；"
        f"只在 registry: {sorted(registered - declared)}"
    )
