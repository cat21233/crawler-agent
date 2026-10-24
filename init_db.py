"""建表脚本：python init_db.py

幂等：create_all 只建「还不存在的表」，已存在的表一个字不改。
⚠️ 加字段/改字段它管不了 —— 那是 Alembic 的事（stage-3）。
"""
from db import init_db

if __name__ == "__main__":
    init_db()

    # 自查：把 engine 眼里的表名列出来，别只看「没报错」
    from sqlalchemy import inspect

    tables = sorted(inspect(init_db.__globals__["engine"]).get_table_names())
    print(f"建表完成，共 {len(tables)} 张：")
    for t in tables:
        print("  -", t)
    assert len(tables) == 5, f"期望 5 张表，实际 {len(tables)} 张：{tables}"
    print("✅ 5/5，验收过")
