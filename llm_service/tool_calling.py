import json
from agent.registry import execute
from agent.schemas import ALL_TOOLS

from llm_service.factory import get_client, get_model
TOOLS = ALL_TOOLS

async def call_one_tool(client, model, messages) -> bool:
    """跑一轮。True = 模型要调工具（已执行+回传）；False = 模型给最终答案了"""
    resp = await client.chat.completions.create(
        model=model, messages=messages, tools=TOOLS
    )
    msg = resp.choices[0].message
    messages.append(msg)
    if not msg.tool_calls:                 # 情况1：模型没要工具 → 告诉外层"停"
        return False

    for tc in msg.tool_calls:              # 情况2：逐个执行
        try:
            # 无参工具（list_targets）可能给空字符串，json.loads("") 会炸
            args = json.loads(tc.function.arguments or "{}")
        except json.JSONDecodeError as e:
            # 模型吐了非法 JSON —— 这是常态，回填错误让它自己改
            result = {"ok": False, "error": f"arguments 不是合法 JSON: {e}"}
        else:
            # execute 内部已把工具异常转成 {"ok": False, ...}，这里不会抛
            result = execute(tc.function.name, args)

        messages.append({
            "role": "tool",
            # content 必须是字符串：SDK 不校验对象，会原样发出、由服务端拒绝
            "content": json.dumps(result, ensure_ascii=False, default=str),
            "tool_call_id": tc.id
        })
    return True                           # 告诉外层"再来一圈"


async def run_tool_loop(user_input: str, max_turns: int = 5, client=None, model: str | None = None) -> str:
    """完整 while 循环，返回模型的最终回答

    client / model 不传就用真 client（生产）；测试传假 client 打桩。
    """
    if client is None:
        client = get_client()
    if model is None:
        model = get_model()
    messages = [{"role": "user", "content": user_input}]
    for turn in range(max_turns):
        has_tool = await call_one_tool(client=client, model=model, messages=messages)
        if not has_tool:
            return messages[-1].content
    return "(已达到最大轮数，仍未得出结论)"
