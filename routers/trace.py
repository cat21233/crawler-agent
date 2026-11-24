from fastapi import APIRouter, HTTPException, Depends
from sqlalchemy.orm import Session

from db import get_db
from db_models import AgentTrace

router = APIRouter(prefix="/traces", tags=['traces'])


@router.get("/{thread_id}")
def get_trace(thread_id: str, db: Session = Depends(get_db))-> dict:
    """按 thread_id 回放一次运行的完整步骤链。

       排序必须是 (step, id)：step 只排「第几轮」，同一轮里的多次工具调用
       全靠 id（自增 = 插入顺序 = 执行顺序）区分先后。只写 ORDER BY step 时，
       step 相同的几行顺序由 MySQL 执行计划决定，不保证稳定。
       """
    rows = (
        db.query(AgentTrace)
        .filter(AgentTrace.thread_id == thread_id)
        .order_by(AgentTrace.step, AgentTrace.id)
        .all()
    )
    if not rows:
        raise HTTPException(status_code=404, detail=f"thread_id={thread_id}没有执行记录")

    steps = [
        {
            "step": t.step,
            "role": t.role,
            "tool_name": t.tool_name,
            "action": t.action,
            "observation": t.observation,
            "status": t.status,
            "error": t.error,
            "latency_ms": t.latency_ms,
            "created_at": t.created_at.isoformat() if t.created_at else None,
            "thought": t.thought,
            "tokens": t.tokens,
        }
        for t in rows
    ]

    return {
        "thread_id": thread_id,
        "count": len(steps),
        "total_latency_ms": sum((t.latency_ms or 0) for t in rows),
        "steps": steps
    }