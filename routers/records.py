"""GET /records —— 把采集结果按 target 拉出来，给前端表格用。"""
import json

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from db import get_db
from db_models import Record

router = APIRouter(prefix="/records", tags=["records"])


@router.get("")
def list_records(
    target_id: int | None = None,
    limit: int = Query(100, ge=1, le=1000),
    db: Session = Depends(get_db),
) -> list[dict]:
    """limit 用 Query(ge/le) 卡死范围：不卡的话 ?limit=99999999 能一次拉穿内存。"""
    q = db.query(Record)
    if target_id is not None:
        q = q.filter(Record.target_id == target_id)
    rows = q.order_by(Record.id.desc()).limit(limit).all()
    return [
        {
            "id": r.id,
            "target_id": r.target_id,
            "title": r.title,
            "price": r.price,
            "url": r.url,
            # extra 列是 TEXT 里的 JSON 串，在这里解开 —— 前端拿到的就是对象
            "extra": json.loads(r.extra) if r.extra else {},
            "crawled_at": r.crawled_at.strftime("%Y-%m-%d %H:%M:%S") if r.crawled_at else "",
        }
        for r in rows
    ]
