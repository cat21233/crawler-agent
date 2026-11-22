# crawler-agent

[![CI](https://github.com/cat21233/crawler-agent/actions/workflows/ci.yml/badge.svg)](https://github.com/cat21233/crawler-agent/actions/workflows/ci.yml)

**模型决定「抓什么、翻到第几页」，爬虫工具负责执行；每一步都落进 `agent_traces`，可以按会话回放。**

## 一条命令起全栈

```bash
cp .env.example .env        # 只需要改一处：填一个 LLM key（deepseek / agnes / zhipu 任选其一）
docker compose up -d --build
# 打开 http://127.0.0.1:8010
```

三个容器：`app`（FastAPI + SSE）、`mysql`（8.0，数据在具名卷里）、`mock`（本地演示站，用于离线跑通）。
容器 MySQL 落在宿主机 **3307**，不占用本机已有的 3306。

> **不填 LLM key 会怎样**（11-22 实测）：`app` 启动时 `validate_settings()` 抛 `ConfigError`，
> lifespan 失败 → 进程退出码 3 → 因为配了 `restart: unless-stopped`，**容器会以约 1.7 秒一次的频率无限重启**，
> `docker compose ps` 里看到的是 `Restarting (3)` 而不是 `Exited (3)`。
> 排查入口只有一个：`docker logs crawler-agent-app-1 --tail 20`。
> 全流程记录见 `docs/evidence/compose-clean-run.md`。

## 它做什么

一句话指令 → Agent 在 7 个工具里自己选（`list_targets` / `fetch_page` / `extract_links` / `count_matches` / `parse_target` / `parse_kv_table` / `save_records`）→ 每一步经 SSE 推给前端 → 结果写 MySQL（`uq_target_url` 幂等去重）→ 前端表格可查 → 全过程写 `agent_traces`。

## 实测数字

| 指标 | 值 | 复现方式 |
| --- | --- | --- |
| 真站采集 | books.toscrape.com **60 条 / 3 页**，去重 **60/60** | `docs/evidence/books-paging.md` |
| 幂等性 | mock 站重复运行，records 稳定在 **10 条**不增长 | `uq_target_url` 唯一约束 + `save_records` 批次内 seen 去重 |
| 单次运行 | **12 步 / 7.82 秒**（thread `20261120_144200_781d`） | `curl http://127.0.0.1:8010/traces/20261120_144200_781d` |
| 工具统计 | 截至 2026-11-21：**32 次调用 / 3 次失败**，最慢 `fetch_page` 平均 3.7 s、峰值 14.3 s | `curl http://127.0.0.1:8010/stats/tools` |
| 上下文压缩 | 单页 **51274 字符 → ref 13 字符，3944 倍**（字符数口径，非 token） | 见下方命令 |
| 测试 | **27 passed** | `.venv/Scripts/python.exe -m pytest -q`（需本机 3306 MySQL） |

```bash
# 上下文压缩的复现命令（字符数口径，不是 token 计数）
python -c "import httpx;r=httpx.get('https://books.toscrape.com/',headers={'User-Agent':'Mozilla/5.0'},trust_env=False,timeout=20);n=len(r.text);print(f'{n} 字符 -> ref 13 字符，缩小 {n/13:.0f} 倍')"
```

失败的 3 次没有隐藏：2 次是探测选择器期间的无效请求，1 次是 `field_map` 结构错误（该错误被模型自行诊断并如实报告）。全部可在 traces 里按 `status='error'` 回放定位。

## 为什么不是"写死规则的爬虫脚本"

**1. 翻页靠模型决策，不靠 hardcode**

mock 站用 `a.next` 能翻页；换成 books.toscrape.com，同一个选择器返回 **0 条**（它的真实结构是 `li.next a`）。

危险之处在于它**不报错**——`extract_links` 返回 `{'links': [], 'total': 0}`，模型据此判断"没有下一页，抓完了"，然后报告"成功"。判据只能靠数量对账：3 页 × 20 本 = 60，少一条都要追。这也是 `count_matches`（只数元素、不取内容）存在的理由——它提供期望值。

**2. 大对象留在服务端（ref 模式）**

一页 HTML 51274 字符，塞进 messages 会被每轮全量重发；实测 `fetch_page` 平均 3.7 s、最慢 14.3 s，说明这页本来就贵。所以 `fetch_page` 只返回 13 字符的取件码 `ref`，模型要看细节时再拿 `ref` 换——单轮上下文缩小 3944 倍。

**3. 失败必须可见**

`agent_traces` 一行一个 step：`tool_name` / `status` / `latency_ms` / `error` / `observation`（只存摘要，HTML 进 `page_cache`）。`GET /traces/{thread_id}` 按 `ORDER BY step, id` 回放；`GET /stats/tools` 按工具聚合调用数与错误数。

## 可观测性

```
GET /traces/20261120_144200_781d
{"thread_id":"20261120_144200_781d","count":12,"total_latency_ms":7815,
 "steps":[{"step":1,"tool_name":"list_targets","status":"ok","latency_ms":2,"role":"tool"},
          {"step":2,"tool_name":"fetch_page","status":"ok","latency_ms":1804,"role":"tool"}, ...]}

GET /stats/tools
{"tools":[{"tool_name":"fetch_page","calls":9,"errors":2,"avg_latency_ms":3701,"max_latency_ms":14329},
          {"tool_name":"parse_target","calls":6,"errors":1,"avg_latency_ms":23,"max_latency_ms":98},
          {"tool_name":"save_records","calls":5,"errors":0,"avg_latency_ms":16,"max_latency_ms":21}, ...],
 "total_calls":32,"total_errors":3}
```

> `ORDER BY step, id` 里的 `id` 不能省：一个 step 可能产出多条 tool 记录，只按 step 排会串。

## MVP 验收（七条，逐条有证据）

| # | 要求 | 证据 |
| --- | --- | --- |
| 1 | 自然语言指令驱动 | `POST /agent/run {"input":"用 target_id=2 抓取 books 前 3 页并入库"}` |
| 2 | ≥2 个工具被真实调用 | 单次运行 12 次工具调用，覆盖 5 个工具（见 `/stats/tools`） |
| 3 | SSE 逐条推送 | 8 类事件契约：`docs/sse-events.md`；原始流：`docs/evidence/books-paging.log` |
| 4 | MySQL 落库 + 幂等 | `uq_target_url`；真站 60 条去重 60/60 |
| 5 | traces 可回放 | `GET /traces/{thread_id}` → 12 步 / 7.82 s |
| 6 | 前端表格可见 | `docs/evidence/frontend-table.png` |
| 7 | 一条命令起全栈 | `docker compose up -d --build`，三容器 healthy |

## 技术取舍

| 决策 | 选择 | 为什么不选另一个 | 详见 |
| --- | --- | --- | --- |
| 采集结果存储 | 单表 `records` + `extra` JSON 文本列 | 一站点一表会把「加站点」从改配置变成改代码，且毁掉通用查询与通用前端 | `docs/storage-design.md` |
| 页面数据传递 | ref 模式（服务端缓存 + 取件码） | 直接返回 HTML 会在 12 轮循环里被重复计费 | 本文「为什么不是脚本」第 2 条 |
| 流式协议 | SSE（`fetch` + `ReadableStream`） | `EventSource` 只支持 GET，带不了请求体 | `docs/sse-events.md` |
| 限流处理 | 工具返回 `retryable` + `retry_after`，由模型决策 | 把 429 抛成异常字符串，模型会当成"抓取失败"反复重试 | `docs/evidence/bridge3-rate-limit.log` |

## 已知限制

1. **`extra` 不可索引**：数值筛选只能在 Python 里做，且按字符串排序会错（`"10" < "9.7"`）。升级路径（MySQL JSON 列 + 生成列索引）已写进 `docs/storage-design.md` 第 7 节。
2. **`page_cache` 在内存**：进程重启即丢，且只在单 worker 下安全；多 worker 需换 Redis。
3. **SPA 站点不支持**：`targets.need_render` 字段留了位，Playwright 渲染未接。
4. **无接口鉴权**：`JWT_SECRET_KEY` 的启动校验已就位，但路由还没挂 `Depends`，当前只能本机/内网使用。

## 目录结构

```
agent/       loop.py（主循环）· tools.py（7 个工具）· trace.py（trace 落库）· prompts.py · schemas.py
routers/     agent_run.py（SSE）· targets.py · records.py · trace.py · stats.py
common/      page_cache.py（ref 缓存）· sse.py · logger.py
static/      index.html + app.js（fetch 读流 + 增量分帧 + 表格）
mock_site/   本地演示站（3 页 × 5 商品 + 10 个详情页）
docs/        设计定稿 · 审查报告 · storage-design.md · sse-events.md · evidence/
tests/       27 passed
```

## 开发环境注意（全是实跑踩过的）

- **中文不要走命令行参数**：`curl -d '{"a":"中文"}'` 会破坏编码 → `error parsing the body`；改用 `--data-binary @file`。
- **容器时区**：默认 UTC，`now()` 落库比本地差 8 小时 → compose 里给 mysql 设 `TZ: Asia/Shanghai`。
- **`Settings` 的严格模式**只对「读 `.env` 文件」生效、对「环境变量」不生效——`.env` 混入 compose 专用变量时，容器正常而本地 pytest 报 `extra_forbidden`。
- **`restart` 策略要三个服务一致**：只给 app 配 `unless-stopped`，Docker Desktop 重启后会变成 app 单飞崩溃循环。
