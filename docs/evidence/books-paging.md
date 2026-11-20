# books.toscrape.com 翻页 e2e 实证（2026-11-20）

> 对应 plan c-122（11-19 回补）。所有数字均为本次实跑输出。

## 1. 为什么要「探」选择器：实测对照

```
fetch_page('https://books.toscrape.com/')
  -> {'ref': 'page_72b5092e', 'url': 'https://books.toscrape.com/', 'chars': 51274}

extract_links(ref, 'li.next a')  -> {'links': ['https://books.toscrape.com/catalogue/page-2.html'], 'total': 1}
extract_links(ref, 'a.next')     -> {'links': [], 'total': 0}
count_matches(ref, 'li.next')    -> 1
```

**mock 站用 `a.next` 能过，books 站是 0 条** —— 它的翻页是 `<li class="next"><a href>`，a 嵌在 li 里。
照搬 mock 的选择器 = 翻不动页，而且不报错（0 条链接，模型会以为只有一页）。

## 2. 两页的 href 形态不同（解析基准问题）

| 页面 | 卡片 href 原文 | 用 `target.url`（站点根）拼接 |
| --- | --- | --- |
| 首页 | `catalogue/a-light-in-the-attic_1000/index.html` | ✅ 正确 |
| page-2 | `in-her-wake_980/index.html`（相对 `/catalogue/`） | ❌ 少一段 `/catalogue/` → 404 |

修复：`parse_target` 的解析基准从 `t.url` 改成 `get_cache().get_url(ref)`（这一页抓取时的真实 URL）。
`target.url` 只是**入口地址**，不是解析基准。

修复后实证：`SELECT COUNT(*) WHERE url NOT LIKE '%/catalogue/%'` = **0**，抽查链接 HTTP **200**。

## 3. 另外两个只有真站才暴露的坑

| 坑 | 现象 | 修法 |
| --- | --- | --- |
| 标题截断 | `h3 a` 的**文本**是 `A Light in the ...`（站点自己截断），`attr="title"` 才是全名 | field_map 用 `{"sel":"h3 a","attr":"title"}` |
| `class` 是多值属性 | `<p class="star-rating One">` → bs4 返回 `["star-rating","One"]`，直接进 `extra` 就是数组 | `_pick` 里 list → `" ".join()` |

修复后实测：`extra.rating` = `"star-rating Five"`（字符串），`extra.stock` = `"In stock"`。

## 4. 最终 field_map

```json
{"card": "article.product_pod",
 "name":  {"sel": "h3 a", "attr": "title"},
 "url":   {"sel": "h3 a", "attr": "href", "join": "base"},
 "price": {"sel": "p.price_color", "cast": "money"},
 "rating":{"sel": "p.star-rating", "attr": "class"},
 "stock": {"sel": "p.instock.availability"}}
```

## 5. 真模型执行结果

请求：`{"input":"用 target_id=2 抓取 books 前 3 页并入库","max_turns":14}`

| 事件类型 | 次数 |
| --- | --- |
| start | 1 |
| tool_calls（模型轮次） | 8 |
| tool_start / tool_end | 12 / 12 |
| answer / done | 1 / 1 |

落库：

```
target_id  n   uniq
       1  10     10     (mock，11-18)
       2  60     60     ← 3 页 × 20 本，url_hash 去重零损失
```

- 页数：3（首页 → page-2 → page-3，均通过页面真实 next 链接发现）
- 第 3 页后主动停止，未抓第 4 页
- `crawled_at` = `2026-11-20 14:42:28`（与本地时钟一致，容器 TZ 修复生效）
- 无 429、无空数组、无解析失败

证据原文：`docs/evidence/books-paging.log`（6456 字节，完整 SSE 事件流）

## 6. 本次一起修掉的配置/环境问题

| 问题 | 根因 | 处置 |
| --- | --- | --- |
| pytest 全红：`Extra inputs are not permitted` | `.env` 里加了 compose 专用 `MYSQL_*`，撞 Settings 严格模式（且只对「读 .env 文件」路径生效，容器不受影响） | `config.py` 的 `SettingsConfigDict(extra="ignore")` |
| `crawled_at` 差 8 小时 | 容器时区 UTC，`now()` 取服务器时间 | `docker-compose.yml` mysql 加 `TZ: Asia/Shanghai` + `--force-recreate` |
| 重建 mysql 后 app `Exited (3)` | lifespan 里 `init_db()` 连库被拒 → 启动失败；无 restart 策略 | app 加 `restart: unless-stopped`（注释写明只能兜「退出」） |
| `curl -d` 中文 body → `error parsing the body` | 中文经 curl.exe 命令行参数，编码被破坏 | body 写文件 + `--data-binary @file`（或纯 ASCII 输入） |
| `$'\E[200~printf': command not found` | mintty 括号粘贴转义未被 bash 消费 | `bind 'set enable-bracketed-paste off'` |
| `ERROR 2002 socket` | 容器刚重建 2 秒，mysqld 未监听 unix socket | 查询一律加 `-h127.0.0.1` 走 TCP |

**六条里有五条与项目业务代码无关**，但每条都能吃掉半小时——这类「环境边界」问题写进 README 的开发笔记，比再写三个 mock 用例更有用。
