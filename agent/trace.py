
import time          # 这个文件里其实只有 loop 用，trace.py 可能不需要
import uuid
from datetime import datetime

from db import SessionLocal
from db_models import AgentTrace



def new_thread_id() -> str:
    """生成 <日期>_<时间>_<4位随机> 形态的会话标识。

    时间部分给人看（翻日志时能立刻定位是哪次运行），
    随机后缀防同一秒内两次运行撞成同一个 thread_id。
    """
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    suffix = uuid.uuid4().hex[:4]
    return f"{stamp}_{suffix}"
def save_trace(
        thread_id :str,
        step: int,
        role: str,
        tool_name: str | None = None,
        action: str | None = None,
        observation: str | None = None,
        status: str = "ok",
        error: str | None = None,
        latency_ms: int | None = None,
) -> None:
    if observation is not None and len(observation) > 2000:
        total = len(observation)
        observation = observation[:2000] + f"...(已截断，原长{total})"
    s = SessionLocal()
    try:
        s.add(AgentTrace(
            thread_id=thread_id,
            step=step,
            role=role,
            tool_name=tool_name,
            action=action,
            observation=observation,
            status=status,
            error=error,
            latency_ms=latency_ms
        ))
        s.commit()
    except Exception as e:
        # 只 print，不抛
        s.rollback()
        print(
            f"[trace] 落库失败 thread={thread_id} step={step}: "
            f"{type(e).__name__}: {e}",
            flush=True,
        )

    finally:
        s.close()