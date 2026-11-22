# c-107 从零预演：`docker compose up` 在全新环境是否成立

> 日期：2026-11-22 13:27~13:40
> 目的：模拟"面试官 clone 仓库后照 README 跑"，环境还原到 **空数据卷 + 只有 `.env.example`**。
> 方式：`down -v` 清卷 → 按 README 两条命令 → 观察每个容器的真实状态。

---

## 一、预演前基线（备份依据）

| 项 | 值 |
| --- | --- |
| records | target 1 → 10（mock）· target 2 → 60（books）· 合计 **70** |
| agent_traces | **32** |
| targets | **2** |
| 备份 | `mysqldump --single-transaction` → 58 KB / 4 条 INSERT |
| 配置备份 | `.env` 23 行 → `.env.real.bak` |

---

## 二、按 README 执行的结果（第一轮：`.env.example` 原样复制）

```bash
cp .env.example .env
docker compose down -v          # 删容器 + 网络 + 具名卷
docker compose up -d --build    # real 0m21.906s
```

**容器状态（12 秒后）**

| 容器 | 状态 | 判定 |
| --- | --- | --- |
| `crawler-agent-mysql-1` | `Up 34 seconds (healthy)` | ✅ 正常 |
| `crawler-agent-mock-1` | `Up 18 seconds` | ✅ 正常 |
| `crawler-agent-app-1` | **`Restarting (3) 1 second ago`** | ❌ 崩溃循环，`RestartCount=7`、`Restarting=true` |

**app 日志（真实输出）**

```
File "/app/main.py", line 29, in lifespan
    validate_settings()
File "/app/config.py", line 85, in validate_settings
    raise ConfigError(
config.ConfigError: JWT_SECRET_KEY 缺失或太短（要求 ≥32 字符）。
  生成一个：python -c "import secrets; print(secrets.token_urlsafe(48))"，然后写进项目根的 .env：JWT_SECRET_KEY=<生成的值>

ERROR:    Application startup failed. Exiting.
```

**结论：README 的「一条命令起全栈」在全新环境下不成立。** 缺的是 `JWT_SECRET_KEY` —— README 只提了"填一个 LLM key"。

---

## 三、判别实验：只补 JWT，LLM key 仍留空

用来分离"两道校验"的顺序，避免把两个原因混成一个。

```bash
# 把 JWT_SECRET_KEY 填成 47 字符，DEEPSEEK_API_KEY 保持空
docker compose up -d --force-recreate app
```

```
File "/app/config.py", line 94, in validate_settings
    raise ConfigError("一个厂商都没有配去.env里面补")
config.ConfigError: 一个厂商都没有配去.env里面补
ERROR:    Application startup failed. Exiting.
```

**两道校验的真实顺序**（`config.py:74-98`）：

| 顺序 | 检查 | 失败信息 |
| --- | --- | --- |
| ① | `len(jwt_secret_key.strip()) >= 32` | `JWT_SECRET_KEY 缺失或太短（要求 ≥32 字符）` |
| ② | 至少一个厂商配了 key | `一个厂商都没有配去.env里面补` |
| ③ | 当前 `LLM_PROVIDER` 在那个列表里 | `当前厂商 X 没有配置 API key` |

所以"填一个 LLM key"是必要不充分的 —— **JWT 那道先拦**。

---

## 四、通过后的真实数字（恢复完整 `.env`）

```bash
cp .env.real.bak .env
docker compose up -d --force-recreate app
# 第 4 次探测（每 2 秒一次）→ healthy，约 7~8 秒
```

| 检查 | 结果 |
| --- | --- |
| `/health` | `{"status":"ok","app":"crawler-agent"}` **200** |
| 五张表 | agent_traces / records / targets / tasks / threads —— 全部由 `init_db()` 在空库上建成 |
| 数据恢复 | 10 + 60 = **70 records**、**32 traces**、**2 targets**，与预演前完全一致 |

**顺带验证的两件既有设计**（不是新写的）：

- `depends_on: condition: service_healthy` 生效：日志顺序是 `mysql Healthy` → `app Starting`，app 没有撞就绪窗口、没有出现 `Can't connect to MySQL server`。
- `restart: unless-stopped` 生效：13:24 启动 Docker Desktop 后三个容器自己起来了（11-21 加的策略）。

---

## 五、两个必须写进 README 的发现

### 1. `cp .env.example .env` 之后起不来（已修）

- **修法**：`.env.example` 里预置一个 dev 专用 `JWT_SECRET_KEY`（47 字符）+ 注释说明"生产必须换"。
- **理由**：README 承诺的是"只填一个 LLM key 就能跑"，这个承诺必须为真；否则每个 clone 的人都会先撞一次 `ConfigError`。

### 2. `restart: unless-stopped` 让"启动必错"变成无限重启（已写进 README 提示）

| 有 restart 策略 | 无 restart 策略 |
| --- | --- |
| `Restarting (3)`，12 秒内重启 7 次 | `Exited (3)`，一眼看到已停止 |

**这是同一个策略的两面**：11-21 加它是为了"Desktop 重启后服务自动回来"，代价是"配置错时表现为无限重启"，`ps` 里看着像"活着"。判据：**`ps` 看到 `Restarting` 一律去看 `logs`，不要以为服务在跑。**

---

## 六、复现命令（全部可粘贴）

```bash
cd C:\Users\yizihao\projects\crawler-agent

# 1. 备份（切记：down -v 会删数据）
docker exec crawler-agent-mysql-1 mysqldump -h127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" --single-transaction crawler_agent > db-backup.sql
cp .env .env.real.bak

# 2. 还原成"全新机器"
cp .env.example .env
docker compose down -v
docker compose up -d --build

# 3. 观察
docker compose ps -a
docker logs crawler-agent-app-1 --tail 20
docker inspect crawler-agent-app-1 --format 'RestartCount={{.RestartCount}} ExitCode={{.State.ExitCode}}'

# 4. 恢复
cp .env.real.bak .env
docker compose up -d --force-recreate app
docker exec -i crawler-agent-mysql-1 mysql -h127.0.0.1 -uroot -p"$MYSQL_ROOT_PASSWORD" crawler_agent < db-backup.sql
```
