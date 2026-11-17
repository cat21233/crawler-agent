"""Agent 主循环的控制流测试：轮数不能被第一轮判死。

为什么必须专门测（11-17 实测事故）：
    「跑到上限」那段收尾被写进了 for 循环内部，与 if not has_tool 同级。
    后果：第一轮只要有工具调用（has_tool=True）就跳过 if、直接撞到收尾，
    喊一句「跑到上限 12 轮」然后 return。12 轮变 1 轮，trace 表只有 1 条。
    前端看到的是「完成 + 一段跑满的抱怨」，像是模型不行，实际是缩进错位。

这类 bug 的可怕之处：不报错、不抛异常、日志也像正常结束。
唯一的判据是「用了几个工具 ≠ 1」和「答案不是收尾文案」，所以必须断言这两条。

为什么不碰数据库 / 不碰网络：
    工具名故意用不存在的（registry.execute 会返回 ok=False 而不是抛），
    save_trace 打成空操作。测的是控制流，不是工具能力。
"""
import json

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

import agent.loop as loop_mod
from main import app

client = TestClient(app)


# 下面三个 helper 与 tests/test_sse.py 同源，故意复制而不是 import：
# tests/ 不是包，跨文件 import 依赖 pytest 的 rootdir 行为，CI 上换个调用方式就断。
def _resp(content=None, tool_calls=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def _tool_call(name: str, arguments: str = "{}", call_id: str = "call_1"):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


class _FakeClient:
    """按脚本逐轮返回响应。取完就抛 IndexError，暴露「轮数比预期多」。"""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        return self._scripted.pop(0)


def _answers(body: str) -> list[str]:
    """从 SSE 文本里抠出所有 answer 事件的 content。"""
    out = []
    for block in body.split("\n\n"):
        if "event: answer" in block and "data: " in block:
            out.append(json.loads(block.split("data: ", 1)[1])["content"])
    return out


@pytest.fixture
def no_db(monkeypatch):
    monkeypatch.setattr(loop_mod, "save_trace", lambda *a, **k: None)


def test_tool_calls_do_not_end_the_loop(monkeypatch, no_db):
    """3 轮工具调用 + 第 4 轮答案 → 答案必须是第 4 轮那句，不是「跑到上限」。

    这条就是本次事故的回归测试：缩进一错，答案立刻变成收尾文案。
    """
    fake = _FakeClient([
        _resp(tool_calls=[_tool_call("nope_a", "{}", "c1")]),
        _resp(tool_calls=[_tool_call("nope_b", "{}", "c2")]),
        _resp(tool_calls=[_tool_call("nope_c", "{}", "c3")]),
        _resp(content="抓完了，共 15 条"),
    ])
    monkeypatch.setattr(loop_mod, "get_client", lambda: fake)
    monkeypatch.setattr(loop_mod, "get_model", lambda: "fake-model")

    r = client.post("/agent/run", json={"input": "随便", "max_turns": 12})

    assert r.status_code == 200
    body = r.text
    ans = _answers(body)
    assert ans == ["抓完了，共 15 条"], f"answer 事件应只有第 4 轮那句，实际 {ans}"
    assert "跑到上限" not in body
    # 三轮工具都真的执行了（不是第 1 轮就退）
    assert body.count("event: tool_start") == 3


def test_max_turns_exhausted_emits_answer(monkeypatch, no_db):
    """每轮都要工具、永远不给答案 → 跑满 max_turns 后必须发 answer，不能留空白。"""
    fake = _FakeClient([
        _resp(tool_calls=[_tool_call("nope", "{}", f"c{i}")]) for i in range(2)
    ])
    monkeypatch.setattr(loop_mod, "get_client", lambda: fake)
    monkeypatch.setattr(loop_mod, "get_model", lambda: "fake-model")

    r = client.post("/agent/run", json={"input": "随便", "max_turns": 2})

    body = r.text
    ans = _answers(body)
    assert len(ans) == 1, f"跑满也必须且只能发一条 answer，实际 {ans}"
    assert "跑到上限 2 轮" in ans[0]
    assert body.count("event: tool_start") == 2      # 两轮都跑了，没提前收尾
