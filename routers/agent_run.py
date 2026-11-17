"""POST /agent/run —— 用 SSE 把 Agent 的执行过程边跑边推。"""
import asyncio
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from agent.loop import run_agent

router = APIRouter(prefix="/agent", tags=["agent"])


class RunRequest(BaseModel):
    input: str
    max_turns: int = 12


@router.post("/run")
async def run(req: RunRequest) -> StreamingResponse:
    """跑一次 Agent，把每一步作为 SSE 事件推给客户端。

    两个关键点（写错了不报错，只是"看起来像流式"）：

    1. 必须先用 create_task 把活丢到后台，再返回 StreamingResponse。
       如果先 await run_agent 再返回，响应体会在全部跑完后一次性刷出去。

    2. 回调和生成器之间必须用 asyncio.Queue 桥接：
       回调是同步的（put_nowait），生成器是 async 的（await get）。
    """
    q: asyncio.Queue = asyncio.Queue()

    async def worker() -> None:
        try:
            ans = await run_agent(
                req.input,
                max_turns=req.max_turns,
                trace=False,               # 不往 stdout 打，改走事件流
                on_event=q.put_nowait,
            )
            q.put_nowait({"type": "done"})
        except Exception as e:
            q.put_nowait({"type": "error", "error": f"{type(e).__name__}: {e}"})
        finally:
            q.put_nowait(None)             # 哨兵：告诉生成器可以收尾了

    task = asyncio.create_task(worker())

    async def gen():
        try:
            while True:
                ev = await q.get()
                if ev is None:
                    break
                yield f"event: {ev['type']}\ndata: {json.dumps(ev, ensure_ascii=False)}\n\n"
        finally:
            task.cancel()

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",     # 让 nginx 别攒着，逐条转发
        },
    )
