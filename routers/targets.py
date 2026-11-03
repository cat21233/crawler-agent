"""采集目标的 HTTP 接口。

三层分工，别混：
    TargetCreate / TargetUpdate / TargetOut  —— HTTP 契约（Pydantic）
    Target                                  —— 数据库表（SQLAlchemy，db_models 里）
    _target_out()                           —— 两者之间的那道门

为什么要这道门：库里 field_map 是 Text（JSON 字符串），对外必须是对象；
而且 ORM 对象挂着 Session，直接返回它会在 db.close() 之后报
DetachedInstanceError。所以统一在出口处转成纯 dict。
"""

import json

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from db import get_db
from db_models import Target, Task

router = APIRouter(prefix="/targets", tags=["targets"])


# ── HTTP 契约 ────────────────────────────────────────────────────────
class TargetOut(BaseModel):
    """响应形状。field_map 是对象，不是字符串。"""

    id: int
    task_id: int
    url: str
    selector: str
    fields: str
    field_map: dict
    need_render: bool
    last_status: str


class TargetCreate(BaseModel):
    """新建时的入参。id / last_crawled_at 由数据库和自己管，不让客户端指定。"""

    task_id: int
    url: str
    selector: str = ""
    fields: str = "title,price,url"
    field_map: dict | None = None
    need_render: bool = False


class TargetUpdate(BaseModel):
    """部分更新。所有字段都可选 —— 只改客户端传了的那些。"""

    url: str | None = None
    selector: str | None = None
    fields: str | None = None
    field_map: dict | None = None
    need_render: bool | None = None
    last_status: str | None = None


# ── 出口转换 ─────────────────────────────────────────────────────────
def _target_out(t: Target) -> dict:
    """ORM 对象 → 可直接当响应的 dict。"""
    return {
        "id": t.id,
        "task_id": t.task_id,
        "url": t.url,
        "selector": t.selector,
        "fields": t.fields,
        # 老数据 field_map 可能是 NULL，json.loads(None) 会 TypeError
        "field_map": json.loads(t.field_map) if t.field_map else {},
        "need_render": bool(t.need_render),  # MySQL 回来是 0/1
        "last_status": t.last_status,
    }


# ── 读 ───────────────────────────────────────────────────────────────
@router.get("", response_model=list[TargetOut])
def list_targets_api(db: Session = Depends(get_db)) -> list[dict]:
    """列出全部采集目标。Depends 会在请求结束时自动 close 这个 Session。"""
    rows = db.query(Target).order_by(Target.id).all()
    return [_target_out(t) for t in rows]


# ── 写（你写）────────────────────────────────────────────────────────
@router.post("", response_model=TargetOut, status_code=201)
def create_target(payload: TargetCreate, db: Session = Depends(get_db)) -> dict:
    """新建一个采集目标。

    要做的四件事：
      1. 检查 payload.task_id 对应的 task 是否存在 —— targets.task_id 是外键，
         不存在的话 db.commit() 会抛 IntegrityError，变成 500，
         而正确语义是「客户端给错了父资源」→ 404
      2. 造 Target 对象：field_map 要先 json.dumps 成字符串再存
      3. db.add(...) 然后 db.commit()，再 db.refresh(obj) 拿到数据库生成的 id
      4. 返回 _target_out(obj)
    """
    r = db.get(Task, payload.task_id)
    if r is None:
        raise HTTPException(status_code=404, detail="task_id 不存在，请检查任务")


    obj = Target(
        task_id=payload.task_id,
        url=payload.url,
        selector=payload.selector,
        fields=payload.fields,
        field_map=json.dumps(payload.field_map,ensure_ascii=False)if payload.field_map else None,
        need_render=payload.need_render,
    )
    db.add(obj)
    db.commit()
    db.refresh(obj)
    return _target_out(obj)



@router.patch("/{target_id}", response_model=TargetOut)
def update_target(
    target_id: int, payload: TargetUpdate, db: Session = Depends(get_db)
) -> dict:
    """部分更新。只改客户端显式传了的字段。

    两个关键点：
      1. payload.model_dump(exclude_unset=True) 才能区分「没传这个字段」和
         「传了 null」—— 直接 model_dump() 会把没传的也当成 None 覆盖掉
      2. field_map 传进来的如果是 dict，要 json.dumps 才能入 Text 列
    找不到 target_id → HTTPException(404)
    """
    obj = db.get(Target, target_id)
    if obj is None:
        raise HTTPException(status_code=404, detail=f'target_id={target_id}不存在')
    data = payload.model_dump(exclude_unset=True)
    if "field_map" in data and data["field_map"] is not None:
        data["field_map"] = json.dumps(data["field_map"], ensure_ascii=False)
    for k, v in data.items():
        setattr(obj, k, v)
    db.commit()
    db.refresh(obj)
    return _target_out(obj)
