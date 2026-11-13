"""SSE 两类的故障注入测试：tool_error 与 error。

为什么必须专门测：
    docs/sse-events.md 声明了 8 类事件，真跑一次只能触达 6 类。
    正常路径永远不会产生非法 JSON、也不会让 run_agent 抛异常，
    所以这两类不注入故障就一次都跑不到 —— 契约就成了空头支票。

为什么不碰数据库：
    这里测的是「事件有没有发出去」，不是「trace 有没有落库」。
    把 save_trace 打成空操作，测试就不需要 MySQL，将来接 CI 也不用起库。
"""
import json

import pytest
from fastapi.testclient import TestClient
from types import SimpleNamespace

import agent.loop as loop_mod
import routers.agent_run as run_mod
from main import app

client = TestClient(app)


def _resp(content=None, tool_calls=None):
    """造一个 OpenAI 风格的响应对象（只保留 loop 会读的字段）。"""
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls)
            )
        ]
    )


def _tool_call(name: str, arguments: str, call_id: str = "call_1"):
    return SimpleNamespace(
        id=call_id, function=SimpleNamespace(name=name, arguments=arguments)
    )


class _FakeClient:
    """按脚本逐轮返回响应。每次 create 取一条，取完就没了。"""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        return self._scripted.pop(0)


@pytest.fixture
def no_db(monkeypatch):
    """把 trace 落库打成空操作：本文件只关心事件，不关心持久化。"""
    monkeypatch.setattr(loop_mod, "save_trace", lambda *a, **k: None)


def test_tool_error_event(monkeypatch, no_db):
    """模型吐非法 JSON → 流里必须出现 event: tool_error。"""
    # 第 1 轮：给一个坏 JSON 的 arguments；第 2 轮：放弃并给最终回答
    fake = _FakeClient([
        _resp(tool_calls=[_tool_call("list_targets", "{这不是 JSON")]),
        _resp(content="参数格式错误，任务中止"),
    ])
    monkeypatch.setattr(loop_mod, "get_client", lambda: fake)
    monkeypatch.setattr(loop_mod, "get_model", lambda: "fake-model")

    r = client.post("/agent/run", json={"input": "列出目标", "max_turns": 3})

    assert r.status_code == 200
    body = r.text
    assert "event: tool_error" in body

    # 从流里抠出 tool_error 那条 data 解析 —— 断言结构，不赌整段文案
    block = next(b for b in body.split("\n\n") if "tool_error" in b)
    data = json.loads(block.split("data: ", 1)[1])
    assert data["name"] == "list_targets"
    assert data["step"] == 1
    assert "JSON" in data["error"]      # 错误信息必须能看懂是 JSON 的问题

    # 坏参数没有被当成工具调用执行掉
    assert "event: tool_start" not in body


def test_error_event(monkeypatch):
    """run_agent 抛异常 → 流里必须出现 event: error，且不把异常吞掉。"""

    async def boom(*args, **kwargs):
        raise RuntimeError("模拟服务端炸了")

    monkeypatch.setattr(run_mod, "run_agent", boom)

    r = client.post("/agent/run", json={"input": "随便", "max_turns": 1})

    assert r.status_code == 200          # 流已经建立，HTTP 层仍是 200
    assert "event: error" in r.text
    assert "RuntimeError" in r.text      # 异常类型必须透出来，否则前端无从排查
