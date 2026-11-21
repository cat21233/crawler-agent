"""测试公共设施：临时 target 工厂 + 缓存隔离。

为什么要 factory 而不是固定 fixture：
    不同用例需要不同的 field_map（好的 / 空的 / 选择器故意写坏的），
    一个固定 fixture 表达不了。工厂函数按参数现造，用完统一清。

为什么必须清理：
    targets/records 里躺着真实数据（2 个 target、5 条记录）。
    测试写进去不删 = 污染真实数据，11-03 就是这么把 target 1 的配置改坏的。
"""
import json
import pathlib

import pytest

from common.page_cache import get_cache
from db import SessionLocal
from db_models import Record, Target, Task

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]

GOOD_FM = {
    "card": "article.card",
    "sku": {"sel": "@", "attr": "data-sku"},
    "name": {"sel": "h2.name"},
    "price": {"sel": "p.price", "cast": "money"},
    "url": {"sel": "a.detail-link", "attr": "href", "join": "base"},
}


@pytest.fixture(autouse=True)
def clean_cache():
    """每个用例前清空页面缓存（进程内单例，不清会互相污染）。"""
    get_cache()._store.clear()
    get_cache()._url_index.clear()
    yield


@pytest.fixture
def make_target():
    """造临时 target，测试结束连它的 records 和所属 task 一起删干净。

    11-21 修复（CI 首次暴露）：原来 task_id 写死为 1，等于依赖「库里恰好存在
    id=1 的 task」。本地库里早就有 → 27 条一直绿；CI 是全新空库 → 8 条用例
    全挂在 targets_ibfk_1 外键上（Cannot add or update a child row）。
    结论：测试要自己造父行，不能依赖存量数据，否则「本地绿、CI 红」是必然的。
    """
    created: list[int] = []
    task_ids: list[int] = []

    def _make_task() -> int:
        s = SessionLocal()
        try:
            t = Task(thread_id="pytest", instruction="测试用任务", status="done")
            s.add(t)
            s.commit()
            s.refresh(t)
            task_ids.append(t.id)
            return t.id
        finally:
            s.close()

    def _make(field_map: dict | None) -> int:
        s = SessionLocal()
        try:
            t = Target(
                task_id=_make_task(),
                url="http://test.local/",
                selector=(field_map or {}).get("card", ""),
                fields="name,price,url",
                field_map=(
                    json.dumps(field_map, ensure_ascii=False) if field_map else None
                ),
                need_render=False,
                last_status="",
            )
            s.add(t)
            s.commit()
            s.refresh(t)
            created.append(t.id)
            return t.id
        finally:
            s.close()

    yield _make

    s = SessionLocal()
    try:
        # 先删子表再删父表：records -> targets -> tasks 三层外键，反着删会报约束错
        for tid in created:
            s.query(Record).filter(Record.target_id == tid).delete()
            s.query(Target).filter(Target.id == tid).delete()
        for task_id in task_ids:
            s.query(Task).filter(Task.id == task_id).delete()
        s.commit()
    finally:
        s.close()
