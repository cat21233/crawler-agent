# crawler-agent

Agent 做主体、爬虫当工具的采集 Agent。定位：**Agent loop + 全程可观测（agent_traces）+ 爬虫差异化**。

- 设计定稿：`docs/crawler-agent-设计定稿.md`
- 实现级审查（10 处必改）：`docs/crawler-agent-设计定稿-审查-20261020.md`
- 复用自 day01：`common/`（logger/sse）、`llm_service/`（async client + FC 单轮闭环）、`db.py`、`config.py`

## 五张表

```
threads (会话)
   ├── N  agent_traces (每个 step 一行，含复合索引 ix_trace_thread_step)
   └── N  tasks (采集任务)
            └── N  targets (目标配置)
                    └── N  records (抓取记录，uq_target_url 幂等去重)
```

## 快速开始

```bash
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env   # 填 DEEPSEEK_API_KEY
python init_db.py        # 建 5 张表（幂等）
```

## 周计划（定稿第九节）

- W1（10/21-10/27）骨架 + 5 张表 + mock 站，KPI：浏览器看见 5 个商品卡片
- W2（10/28-11/3）Agent 循环 + 4 工具
- W3（11/4-11/10）可观测性（traces 查询/回放/分位数）
- W4（11/11-11/19）SSE 前端（fetch + ReadableStream，不用 EventSource）
