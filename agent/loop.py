"""Agent 主循环：模型要工具 → execute → 结果回填 → 再来一圈。

从 llm_service/tool_calling.py 迁移而来（循环只留这一份）。
相对原版加了两件事：
  1. system prompt（agent/prompts.py）—— 模型不再裸奔上场
  2. 工具调用轨迹（trace）—— 每次调谁、传什么、成没成功打到 stdout。
     这是 W3 agent_traces 表的雏形：先让人看得见，再谈落库。
"""

import json
import time

from agent.prompts import SYSTEM_PROMPT
from agent.registry import execute
from agent.schemas import ALL_TOOLS
from llm_service.factory import get_client, get_model
from agent.trace import new_thread_id,save_trace

TOOLS = ALL_TOOLS  # 发给模型的整包说明书（5 个工具）


def _brief(args: dict, limit: int = 60) -> dict:
    """参数摘要：长字符串（如整页 HTML）只显示长度，不打满屏。"""
    return {
        k: (f"<{len(v)} 字符的字符串>" if isinstance(v, str) and len(v) > limit else v)
        for k, v in args.items()
    }


async def call_one_tool(client, model, messages, thread_id: str, step: int, trace: bool = True, emit=None) -> bool:
    """跑一轮。True = 模型要调工具（已执行+回传）；False = 模型给最终答案了"""
    emit = emit or (lambda kind, **kw: None)
    resp = await client.chat.completions.create(
        model=model, messages=messages, tools=TOOLS
    )
    msg = resp.choices[0].message
    messages.append(msg)
    if not msg.tool_calls:                 # 情况1：模型没要工具 → 告诉外层"停"
        return False
    emit("tool_calls", step=step, names=[tc.function.name for tc in msg.tool_calls])

    for tc in msg.tool_calls:              # 情况2：逐个执行
        try:
            # 无参工具（list_targets）可能给空字符串，json.loads("") 会炸
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError as e:
            # 模型吐了非法 JSON —— 这是常态，回填错误让它自己改
            result = {"ok": False, "error": f"arguments 不是合法 JSON: {e}"}
            save_trace(
                thread_id, step, "assistant",
                tool_name=tc.function.name,
                action=tc.function.arguments,
                status="error",
                error=str(e)
            )
            emit("tool_error", step=step, name=tc.function.name, error=result["error"])

        else:
            # execute 内部已把工具异常转成 {"ok": False, ...}，这里不会抛
            if trace:
                print(f"[tool] {tc.function.name} {_brief(args)}", flush=True)
            emit("tool_start", step=step, name=tc.function.name, args=_brief(args))

            t0 = time.perf_counter()
            result = execute(tc.function.name, args)
            latency_ms = int((time.perf_counter()-t0)*1000)
            out = json.dumps(result, ensure_ascii=False, default=str)
            if trace:

                print(f"       -> ok={result.get('ok')} {out[:120]}", flush=True)
            save_trace(
                thread_id, step, "tool",
                tool_name=tc.function.name,
                action=json.dumps(args, ensure_ascii=False, default=str),
                observation=json.dumps(result, ensure_ascii=False, default=str),
                status="ok" if result.get("ok") else "error",
                error=result.get("error"),
                latency_ms=latency_ms,
            )
            emit(
                "tool_end", step=step, name=tc.function.name,
                ok=result.get("ok"), latency_ms=latency_ms, preview=out[:200],
            )
        messages.append({
            "role": "tool",
            # content 必须是字符串：SDK 不校验对象，会原样发出、由服务端拒绝
            "content": json.dumps(result, ensure_ascii=False, default=str),
            "tool_call_id": tc.id,
        })
    return True


async def run_agent(
    user_input: str,
    max_turns: int = 12,
    client=None,
    model: str | None = None,
    trace: bool = True,
    thread_id: str | None = None,
    on_event=None,
) -> str:
    """完整循环，返回模型的最终回答。

    client / model 不传就用真 client（生产）；测试传假 client 打桩。
    trace=False 可关掉轨迹输出（比如以后挂到 SSE 端点时改走别的地方）。
    """
    def emit(kind:str, **data):
        """把事件交给外部消费者；没传 on_event 就什么都不做。

               为什么用回调而不是 print：
                   print 是终端专用输出，SSE 要的是结构化 dict。
                   字符串和 dict 之间那道解析迟早会变成自己写正则。
               """
        if on_event is not None:
            on_event({"type": kind, **data})

    if thread_id is None:
        thread_id = new_thread_id()
    print(f"[run] thread_id = {thread_id}", flush=True)
    emit("start", thread_id=thread_id)

    if client is None:
        client = get_client()
    if model is None:
        model = get_model()

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_input},
    ]

    for turn in range(1, max_turns + 1):
        if trace:
            print(f"[loop] 第 {turn}/{max_turns} 轮", flush=True)
        has_tool = await call_one_tool(client, model, messages, thread_id, turn, trace=trace, emit=emit)
        if not has_tool:
            emit("answer", content=messages[-1].content)
            return messages[-1].content
    return "(已达到最大轮数，仍未得出结论)"
