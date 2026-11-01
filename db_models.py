"""crawler-agent 五张表：SQLAlchemy 2.0 声明式（与 day01 的 db_models.py 同一套）。

对设计定稿的三处修正（依据 docs/crawler-agent-设计定稿-审查-20261020.md）：
  P0-1  SQLModel → SQLAlchemy 2.0 Mapped。db.py 的 init_db() 走 Base.metadata.create_all()，
        而 SQLModel 的表注册在 SQLModel.metadata —— 照搬定稿会「init_db 成功但一张表都不建」。
  P0-2  agent_traces 补复合索引 (thread_id, step)；__table_args__ 混 Index 时必须用
        tuple 且把 dict 放最后一个元素（dict 形式塞不进 Index）。
  P0-5  时间字段统一 server_default=func.now()（数据库填），不用定稿的 Python 侧 utcnow
        （定稿根本没定义它，且 aware datetime 存 MySQL DATETIME 会丢时区）。
"""
from datetime import datetime

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


class Thread(Base):
    """会话。一次完整的用户对话 = 一个 thread。thread_id 直接当主键（定稿里没人用的 id 删了）。"""

    __tablename__ = "threads"

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    user_id: Mapped[int | None] = mapped_column(default=None, index=True)
    status: Mapped[str] = mapped_column(String(20), default="active")  # active/completed/failed
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Task(Base):
    """采集任务。一条用户指令 = 一个 task。"""

    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    instruction: Mapped[str] = mapped_column(Text)  # 用户原始指令，全文存
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending/running/done/failed
    total_records: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)

    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Target(Base):
    """采集目标。一个 URL + 一套选择器规则。"""

    __tablename__ = "targets"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    url: Mapped[str] = mapped_column(String(500))
    selector: Mapped[str] = mapped_column(String(200), default="")
    # 逗号分隔（"title,price,url"）。P2-3：W2 做 parse_by_selector 时升级为 JSON field_map
    fields: Mapped[str] = mapped_column(String(200), default="title,price,url")
    # 完整字段规格(JSON)。旧的 selector+fields 两列留着不删，过渡期双写。
    field_map: Mapped[str | None] = mapped_column(Text,default=None)
    need_render: Mapped[bool] = mapped_column(default=False)
    last_crawled_at: Mapped[datetime | None] = mapped_column(DateTime, default=None)
    last_status: Mapped[str] = mapped_column(String(20), default="")

    __table_args__ = {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"}


class Record(Base):
    """抓取到的一条数据。url_hash 唯一约束 = 同一目标下同一 URL 只存一次（幂等去重）。"""

    __tablename__ = "records"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    target_id: Mapped[int] = mapped_column(ForeignKey("targets.id"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    price: Mapped[str | None] = mapped_column(String(50), default=None)
    url: Mapped[str | None] = mapped_column(String(500), default=None)
    url_hash: Mapped[str] = mapped_column(String(64), index=True)  # md5(规范化 url)
    extra: Mapped[str | None] = mapped_column(Text, default=None)  # 其他字段 JSON
    crawled_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("target_id", "url_hash", name="uq_target_url"),
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )


class AgentTrace(Base):
    """Agent 执行轨迹。一个 step = 一行 —— 整个项目的简历卖点（可观测性）就落在这张表。

    回放查询必须 ORDER BY step, id（一步可能产出多条 tool trace，光按 step 排会串）。
    """

    __tablename__ = "agent_traces"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    # ── 归属 ──
    thread_id: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), default=None)
    step: Mapped[int] = mapped_column(default=0)
    # ── 三元组 + 角色 ──
    role: Mapped[str] = mapped_column(String(20), default="assistant")  # user/assistant/tool
    thought: Mapped[str | None] = mapped_column(Text, default=None)
    action: Mapped[str | None] = mapped_column(Text, default=None)  # 完整工具调用 JSON
    # 冗余字段（反范式）：action 是 TEXT 没法索引，tool_name 单列可索引 —— 统计
    # 「所有 fetch_page 调用」就靠它
    tool_name: Mapped[str | None] = mapped_column(String(50), index=True, default=None)
    observation: Mapped[str | None] = mapped_column(Text, default=None)  # ★ 只存摘要，HTML 进 page_cache
    # ── 可观测性 ──
    status: Mapped[str] = mapped_column(String(20), default="ok")  # ok/error/blocked
    error: Mapped[str | None] = mapped_column(Text, default=None)
    latency_ms: Mapped[int | None] = mapped_column(default=None)
    tokens: Mapped[int | None] = mapped_column(default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    __table_args__ = (
        # ★ 最常用查询：按会话拉全部步骤并按 step 排序
        Index("ix_trace_thread_step", "thread_id", "step"),
        # dict 必须放 tuple 最后一个元素 —— 这是 SQLAlchemy 的固定约定
        {"mysql_engine": "InnoDB", "mysql_charset": "utf8mb4"},
    )
