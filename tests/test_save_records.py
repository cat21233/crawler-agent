"""save_records 的两条核心承诺：幂等、不写脏 URL。

这两条都对应真实事故：11-03 相对 URL 绕过去重，重复入库 5 条；
11-07 手动验幂等时只看返回的 inserted，没查库，差点漏掉「写了一半」。
"""
import pytest

from agent.tools import save_records
from db import SessionLocal
from db_models import Record
from tests.conftest import GOOD_FM


def _count_records(target_id: int) -> int:
    s = SessionLocal()
    try:
        return s.query(Record).filter(Record.target_id == target_id).count()
    finally:
        s.close()


def test_save_records_idempotent(make_target):
    """同一批跑两次：第二次全跳过，库里还是 2 条。"""
    tid = make_target(GOOD_FM)
    cards = [
        {"name": "A", "price": None, "url": "http://test.local/a.html"},
        {"name": "B", "price": None, "url": "http://test.local/b.html"},
    ]

    first = save_records(tid, cards)
    second = save_records(tid, cards)

    assert first == {"inserted": 2, "skipped": 0}
    assert second == {"inserted": 0, "skipped": 2}
    # 光看返回值不够，必须查库：返回值可能是假的，库里的行数不会
    assert _count_records(tid) == 2


def test_save_records_relative_url_rejected(make_target):
    """相对 URL 直接拒绝，且一条都不许写进去。"""
    tid = make_target(GOOD_FM)

    with pytest.raises(ValueError, match="绝对地址"):
        save_records(tid, [{"name": "x", "price": None, "url": "/a.html"}])

    # 只断言抛异常，防不住「先写了几条再抛」，所以必须查库
    assert _count_records(tid) == 0


def test_save_records_dedup_in_batch(make_target):
    """同一批内 url 重复：只插一条。

    这条测的是 seen 集合——autoflush=False 时刚 add 的对象还没进库，
    只查库查不出来，会一路 add 到 commit 才撞唯一键、整批回滚。
    """
    tid = make_target(GOOD_FM)
    same = {"name": "dup", "price": None, "url": "http://test.local/same.html"}

    result = save_records(tid, [same, dict(same)])

    assert result == {"inserted": 1, "skipped": 1}
    assert _count_records(tid) == 1
