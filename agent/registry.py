"""工具注册表：模型喊的名字 → 真正的 Python 函数 + 它的说明书。

为什么要这一层：
    模型返回的是 JSON（{"name": "fetch_page", "arguments": "{...}"}），
    它不知道 Python 函数在哪。这张表就是「名字 → 可调用对象」的兑换处。
"""

from collections.abc import Callable

from agent import schemas
from agent.tools import (
    fetch_page,
    list_targets,
    parse_target,
    parse_kv_table,
    save_records,
    count_matches,
    RateLimited,
    extract_links
)

# name -> (调用函数, 说明书)
TOOL_REGISTRY: dict[str, tuple[Callable, dict]] = {
    "list_targets": (list_targets, schemas.LIST_TARGETS),
    "fetch_page": (fetch_page, schemas.FETCH_PAGE),
    "parse_target": (parse_target, schemas.PARSE_TARGET),

    "parse_kv_table": (parse_kv_table, schemas.PARSE_KV_TABLE),
    "save_records": (save_records, schemas.SAVE_RECORDS),
    "count_matches": (count_matches, schemas.COUNT_MATCHES),
    "extract_links": (extract_links, schemas.EXTRACT_LINKS)
}


def get_tool(name: str) -> tuple[Callable, dict] | None:
    """按名字取工具。取不到返回 None。

    不抛 KeyError：模型可能编一个不存在的工具名（幻觉），
    这是常态而非 bug，调用方要知道「这个名字不存在」并纠正模型。
    """
    return TOOL_REGISTRY.get(name)


def execute(name: str, arguments: dict) -> dict:
    """按名字执行工具，返回结构化结果 —— 失败也是结果，不往外抛。

    为什么在这里吃掉异常：
        Agent 循环里一个工具失败不该让整个循环崩掉。
        模型需要看到「这次失败了、原因是什么」，才可能自己决定重试或换方案。
        异常在越靠近调用方的地方，越应该变成数据。
    """
    hit = get_tool(name)
    if hit is None:
        return {"ok": False, "error": f"没有名为 {name!r} 的工具", 'retryable': False}

    func, _ = hit
    try:
        return {"ok": True, "result": func(**arguments)}
    except RateLimited as e:
        return {
            "ok": False,
            "error": str(e),
            "retryable": True,
            "retry_after": e.retry_after,
        }
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}", "retryable": False}
