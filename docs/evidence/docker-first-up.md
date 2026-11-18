# Docker 首次 up 实证（2026-11-18）

> 对应 MVP 第 7 条「一条命令起全栈」、plan c-118 / c-119。
> 所有数字均为本次实跑输出，不是估算。

## 1. 命令与结果

```
docker compose up -d --build
```

| 阶段 | 结果 | 耗时 |
| --- | --- | --- |
| 拉取 `mysql:8.0` | Pulled | **501.6 s**（首次，占了大头；后续不再拉） |
| 构建 `crawler-agent:dev` | Built | **559.3 s**（其中 `RUN pip install` 层 47.8 s） |
| 创建 network `crawler-agent_default` | Created | 0.0 s |
| 创建 volume `crawler-agent_mysql_data` | Created | 0.0 s |
| 容器 `mysql` | Healthy | 16.3 s |
| 容器 `app` | Started | 16.5 s |
| 容器 `mock` | Started | 16.7 s |

`20/20` 步全绿，无失败重试。

## 2. 三容器状态

```
NAME                    STATUS                        PORTS
crawler-agent-mock-1    Up About a minute             0.0.0.0:8099->8099/tcp
crawler-agent-app-1     Up About a minute (healthy)   0.0.0.0:8010->8000/tcp
crawler-agent-mysql-1   Up 2 minutes (healthy)        0.0.0.0:3307->3306/tcp
```

端口选择刻意避开宿主机占用：3307（phpStudy 的 5.7 占着 3306）、8010（与 dev_up.bat 一致）。

## 3. 验收项

| 验收 | 命令 | 结果 |
| --- | --- | --- |
| health 接口 | `curl.exe -s http://127.0.0.1:8010/health` | `{"status":"ok","app":"crawler-agent"}` HTTP 200 |
| 前端页 | `curl.exe -s -o /dev/null -w "%{http_code}" http://127.0.0.1:8010/` | 200 |
| mock 站（宿主） | `curl.exe http://127.0.0.1:8099/` | 200 |
| 建表 | `show tables` | threads / tasks / targets / records / agent_traces 五张全在 |

## 4. 容器网络实证（本次最有价值的一条）

在 **app 容器内部**分别访问两个地址：

| 访问目标 | 结果 |
| --- | --- |
| `http://mock:8099/` | **200**，返回 2070 字节 |
| `http://127.0.0.1:8099/` | **URLError: [Errno 111] Connection refused** |

结论：容器有独立网络栈，`127.0.0.1` 指容器自己；跨容器互访只能用 compose 服务名（内置 DNS）。
这也是把 `mock_site/server.py` 的监听地址从硬编码 `127.0.0.1` 改成 `MOCK_HOST` 环境变量（容器里传 `0.0.0.0`）的原因。

## 5. 端到端（容器内跑 Agent）

```
POST /agent/run  {"input":"用 target 1 抓取商品列表并入库","max_turns":8}
```

事件统计：`start 1 / tool_calls 8 / tool_start 10 / tool_end 10 / answer 1 / done 1`
（8 个模型轮次 → 10 次工具调用，其中有轮次并行发了 2 个调用）

落库结果：

```
records = 10 行（index 5 张卡 + page2 5 张卡，Agent 自己翻了页）
agent_traces = 17 行
样例：1 | 机械键盘 K87 青轴 | 499.0 | http://mock:8099/detail/p1.html
      extra = {"meta": "库存 32 ｜ 已售 418", "sku": "K87"}
```

`cast: money` 生效（`¥499¥599` → `499.0`）、`join: base` 生效（相对地址 → 绝对 URL）、`extra` 收了没有独立列的 `meta` / `sku`。

## 6. 本次踩的两个坑

### 坑 1：field_map 字段值必须是带 `sel` 的 dict

灌演示数据时写成 `{"name": "h2.name"}`，`parse_target` 报：

```
ValueError: 字段 'name' 必须是带 sel 的 dict，例如 {'sel': 'h2.name'}
```

正确形状（`agent/tools.py:226` 的 parse_by_selector 文档串）：

```json
{"card": "article.card",
 "name":  {"sel": "h2.name"},
 "price": {"sel": "p.price", "cast": "money"},
 "url":   {"sel": "a.detail-link", "attr": "href", "join": "base"},
 "sku":   {"sel": "@", "attr": "data-sku"}}
```

有意思的是**模型自己把这个报错诊断出来了**，还给出了修法，并且声明「我没有修改采集配置的权限，需由你执行」——收敛能力面 + 如实报告失败的组合在容器里同样成立。

### 坑 2：`mysql` 客户端输出一堆 `????` 不代表数据坏了

Windows 控制台是 GBK，容器内 mysql 客户端按 UTF-8 输出 → 中文显示成 `????`。
**这是显示问题，不是存储问题**。判据：用 Python 读出来 `repr()`：

```python
repr(r.title)  →  '机械键盘 K87 青轴'
```

中文、`｜` 全角符号、emoji 都完好。以后判断「编码坏了没有」不要看控制台，要 `repr()`。

## 7. 复现方式

```bash
cd C:\Users\yizihao\projects\crawler-agent
docker compose up -d --build      # 起
docker compose ps                 # 三 healthy
docker compose logs -f app        # 跟日志
docker compose down               # 停（数据在 volume 里）
docker compose down -v            # 停并删库
```
