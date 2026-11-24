# thought / tokens 落库（c-111）

> 2026-11-23。目标：把模型的思考正文与 token 消耗写进 `agent_traces` 的两个空列。
> 改前这两列从建表起一直是 NULL。

---

## 一、改前状态

| 项 | 状态 |
| --- | --- |
| `agent_traces.thought` | `Text`，NULL |
| `agent_traces.tokens` | `int`，NULL |
| `save_trace()` 签名 | 没有这两个参数（根因） |
| `role='assistant'` 的行 | **只在「模型吐非法 JSON」时写一条**（`loop.py:66` 原位置） |
| 最后一轮（模型给最终答案） | **一条 trace 都不写** —— 在 `if not msg.tool_calls: return False` 处提前返回 |

三个问题叠加的结果：即使加上参数，`tokens` 也没有载体可写。

---

## 二、改动

| 文件 | 改动 |
| --- | --- |
| `agent/trace.py` | `save_trace()` 末尾追加 `thought: str \| None = None`、`tokens: int \| None = None`；`thought` 复用 `observation` 的 2000 字截断规则；`AgentTrace(...)` 增 `tokens=tokens`，并删掉未使用的 `import time` |
| `agent/loop.py` | `msg = resp.choices[0].message` 之后**无条件**写一条 `role='assistant'` 行（`thought=msg.content`、`tokens=resp.usage.total_tokens if resp.usage else None`），位置在 `if not msg.tool_calls` **之前** |
| `agent/loop.py` | JSON 解析失败分支的 `role` 从 `"assistant"` 改为 `"tool"` |

**得到的干净不变式：**

> 每个 step 恰好 **1 条** `role='assistant'` 行 + **N 条** `role='tool'` 行。

有这条不变式，`SUM(tokens)` 就是确定正确的口径：不会被 tool 行按调用次数重复，也不会有同一步两条 assistant 行。

---

## 三、口径决策（这题的真正难点）

`resp.usage.total_tokens` 是**单次 API 调用的总量**，而一个 step 可能产出多行 trace（几个工具调用就几行）。如果把 token 写在每一行上，`SUM(tokens)` 会按行数翻倍 —— 这是 11-13 那条「指标口径必须与 n 同步」的同类问题（当时是改 n 不改切片）。

**决策：只写在 `role='assistant'` 行。** 因为 token 是模型消耗的，不是工具消耗的；assistant 行按步数增长，与 API 调用次数一一对应。

---

## 四、实测结果

一次 mock 站采集（thread `20261123_163345_9a00`，6 步 / 13 行）：

| step | role | tool_name | tokens | thought |
| --- | --- | --- | --- | --- |
| 1 | assistant | — | **2349** | I'll start by looking at the available tar… |
| 1 | tool | list_targets | NULL | NULL |
| 2 | assistant | — | **2692** | I'll crawl target_id=1 (http://mock:8099/)… |
| 2 | tool | fetch_page | NULL | NULL |
| 3 | assistant | — | **2802** | （空） |
| 3 | tool | parse_target | NULL | NULL |
| 4 | assistant | — | **3556** | Parsed 5 products. Let me save them… |
| 4 | tool | save_records | NULL | NULL |
| 4 | tool | extract_links | NULL | NULL |
| 5 | assistant | — | **3699** | There's a next page link. Let me fetch it… |
| 5 | tool | fetch_page | NULL | NULL |
| 6 | assistant | — | **3809** | （空） |
| 6 | tool | parse_target | NULL | NULL |

**汇总：`COUNT(*) = 13`、`SUM(tokens) = 18907`、`SUM(role='assistant') = 6`、`SUM(role='tool') = 7`。**

三条判据全过：每步一条 assistant 行 / tokens 非 NULL / 多数行 thought 有正文。

---

## 五、一个意外发现：token 是递增的

```
step 1  2349
step 2  2692   (+343)
step 3  2802   (+110)
step 4  3556   (+754)
step 5  3699   (+143)
step 6  3809   (+110)
```

**单调递增，不是每轮大致相同。** 原因是 OpenAI 协议的调用无状态：每轮把**全部历史 messages 重新发一遍**，`usage.total_tokens = 本轮 prompt（含所有历史）+ 本轮输出`。

两个结论：

1. `SUM(tokens) = 18907` 就是这次运行的**真实账单**（每轮独立计费，累加即账单）。
2. **这是 ref 模式最有力的证据**：若 `fetch_page` 把 51K 字符的 HTML 塞进 messages，第 2 轮之后每步 prompt 都背着这 51K，token 会以万为单位涨；用 13 字符的 ref 之后，6 步累计才 3809。

> 面试讲「为什么要 ref 模式」时，这组递增曲线比「3944 倍压缩比」更硬 —— 前者是成本曲线，后者只是一个比值。

---

## 六、遗留（转 c-112）

`routers/trace.py` 的响应字典里**没有 `thought` 和 `tokens`** —— 数据在库里，API 取不到，前端与 `/stats` 都用不上。

排到 11-24 的 `c-112`（stats 端点增强：按 thread 聚合 token 与成功率）一起做，省一次镜像重建。

---

## 七、复现

```bash
cd C:\Users\yizihao\projects\crawler-agent
docker compose up -d --build app
docker compose exec -T app python -c "import asyncio; from agent.loop import run_agent; print(asyncio.run(run_agent('crawl target_id=1 product list', max_turns=6)))"

docker exec crawler-agent-mysql-1 mysql -h127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" crawler_agent -e "
SELECT step, role, tool_name, tokens, LEFT(thought,40)
FROM agent_traces
WHERE thread_id=(SELECT thread_id FROM agent_traces ORDER BY id DESC LIMIT 1)
ORDER BY step, id;"
```
