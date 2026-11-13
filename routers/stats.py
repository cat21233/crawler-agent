"""GET /stats/tools —— 按工具聚合调用次数与耗时。

数据全部来自 agent_traces，不额外落表：trace 是原始事实，统计是它的视图。
这样统计永远不会和 trace 对不上（同一份数据只有一个来源）。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import case, func
from sqlalchemy.orm import Session

from db import get_db
from db_models import AgentTrace

router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/tools")
def tool_stats(db: Session = Depends(get_db)) -> dict:
    """按 tool_name 聚合：调用次数、失败次数、平均/最大耗时。

    三个设计决定：
    1. 只统计 tool_name 非空的行。role='assistant' 的纯文本轮次没有工具在跑，
       算进来 GROUP BY 会产生一条 tool_name=null 的伪记录，前端没法渲染。
    2. errors 用 SUM(CASE WHEN ...) 而不是 SUM(status='error')：
       后者依赖 MySQL 把布尔当整数，换库就废。
    3. 排序按调用次数倒序 —— 这个端点最常见的用法是「哪些工具最慢/最常失败」，
       次数最多的排最前，不用前端再排一次。
    """
    calls = func.count(AgentTrace.id)
    errors = func.sum(case((AgentTrace.status == "error", 1), else_=0))
    avg_latency = func.avg(AgentTrace.latency_ms)
    max_latency = func.max(AgentTrace.latency_ms)

    rows = (
        db.query(
            AgentTrace.tool_name.label("tool_name"),
            calls.label("calls"),
            errors.label("errors"),
            avg_latency.label("avg_latency_ms"),
            max_latency.label("max_latency_ms"),
        )
        .filter(AgentTrace.tool_name.isnot(None))
        .group_by(AgentTrace.tool_name)
        .order_by(calls.desc())
        .all()
    )

    tools = [
        {
            "tool_name": r.tool_name,
            "calls": r.calls,
            "errors": int(r.errors or 0),
            # AVG() 在 MySQL 里返回 Decimal，直接塞进 JSON 会变成字符串或报错
            "avg_latency_ms": int(r.avg_latency_ms) if r.avg_latency_ms is not None else None,
            "max_latency_ms": r.max_latency_ms,
        }
        for r in rows
    ]

    return {
        "tools": tools,
        "total_calls": sum(t["calls"] for t in tools),
        "total_errors": sum(t["errors"] for t in tools),
    }
