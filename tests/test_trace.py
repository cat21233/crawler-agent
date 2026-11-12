import uuid

import pytest
from fastapi.testclient import TestClient

from agent.trace import save_trace
from db import SessionLocal
from db_models import AgentTrace
from main import app

client = TestClient(app)      # 注意：不加 with，见下面「坑 1」


@pytest.fixture
def tid():
    t = "test_" + uuid.uuid4().hex[:8]
    yield t
    s = SessionLocal()
    try:
        s.query(AgentTrace).filter(AgentTrace.thread_id == t).delete()
        s.commit()
    finally:
        s.close()


def test_save_and_query(tid):
    """乱序写入，按 (step, id) 查出来必须有序。"""
    save_trace(tid, 2, "tool", tool_name="b", latency_ms=20)
    save_trace(tid, 1, "tool", tool_name="a", latency_ms=10)

    s = SessionLocal()
    try:
        rows = (
            s.query(AgentTrace)
            .filter(AgentTrace.thread_id == tid)
            .order_by(AgentTrace.step, AgentTrace.id)
            .all()
        )
    finally:
        s.close()

    assert [r.step for r in rows] == [1, 2]
    assert [r.tool_name for r in rows] == ["a", "b"]
def test_observation_truncated(tid):
    observation = "x" * 3000
    save_trace(tid, 1, "tool", tool_name="t", observation=observation)
    s = SessionLocal()
    try:
        row = (
            s.query(AgentTrace)
            .filter(AgentTrace.thread_id == tid)
            .one()
        )
        stored = row.observation
    finally:
        s.close()
    assert len(stored) < 3000
    assert "已截断" in stored
    assert "3000" in stored


def test_save_trace_never_raises(tid):
    save_trace("x"*100, 1, "tool", tool_name="t")


def test_get_trace_200(tid):
    save_trace(tid, 1, "tool", tool_name="a", latency_ms=10)
    save_trace(tid, 2, "tool", tool_name="b", latency_ms=20)

    r = client.get(f"/traces/{tid}")

    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert body["total_latency_ms"] == 30
    assert [s["step"] for s in body["steps"]] == [1, 2]


def test_get_trace_404():
    r = client.get("/traces/nope_never_exists")

    assert r.status_code == 404
    assert "没有执行记录" in r.json()["detail"]
