import hashlib
import json
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from db import SessionLocal
from db_models import Record,Target
from common.page_cache import get_cache


class RateLimited(Exception):
    """站点返回 429（Too Many Requests）。

    为什么单独定义一个类，而不是直接 raise HTTPStatusError：
        429 和 404/403 的性质完全不同 —— 404 是「别试了」，429 是「等会儿再来」。
        上层要能把这两种语义分开，模型才知道该放弃还是该等。
    """

    def __init__(self, url: str, retry_after: int | None = None) -> None:
        self.url = url
        self.retry_after = retry_after
        msg = f"{url} 返回 429 限流"
        if retry_after is not None:
            msg += f"，站点建议 {retry_after} 秒后重试"
        super().__init__(msg)


# 浏览器特征。不加这组头，httpx 会发 `User-Agent: python-httpx/0.28.x`，
# 等于隔着门喊「我是脚本」。
# 11-08 实测（改前 → 加头后）：豆瓣 418 → 200、知乎 403 → 200、微博 432 → 200。
# Accept-Language 里的 zh-CN 不能省：有些站按语言返回不同的页面结构，
# 不带它可能拿到英文版，选择器就对不上了。
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": (
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


def fetch_page(url: str, timeout: float = 10.0) -> dict:
    """GET 一个页面，把 HTML 存进服务端缓存，只返回取件码 ref。

    为什么不直接 return r.text：
        tool 结果会被 append 进 messages，而 messages 每轮全量重发。
        一个 48K 的页面在 12 轮循环里会被计费 12 次，直接超 context。
        所以真数据留在服务端，模型手里只拿一个 8 字符的 ref。

    trust_env=False 关掉「读 HTTP_PROXY 等环境变量」：
    带着代理抓 127.0.0.1 时，代理会把「服务没起」的 10061 改写成 502，
    错误信息被扭曲，排查方向会跑偏。要代理就显式传 proxy=，别让环境变量偷偷生效。
    """
    r = httpx.get(
        url=url,
        timeout=timeout,
        follow_redirects=True,
        trust_env=False,
        headers=DEFAULT_HEADERS,
    )

    # 429 单独处理：必须在 raise_for_status 之前，也必须早于 put 进 cache。
    # 早于 put 是有意的——错误页拿不到 ref，模型就没有任何办法把它当数据解析。
    if r.status_code == 429:
        ra = r.headers.get("Retry-After")
        raise RateLimited(url, int(ra) if ra and ra.isdigit() else None)

    r.raise_for_status()

    ref = get_cache().put(r.text, url)   # put 的返回值就是取件码
    return {"ref": ref, "url": url, "chars": len(r.text)}


def parse_cards(html: str, base_url: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card in soup.select("article.card"):
        price_text = card.select_one("p.price").get_text(strip=True)
        out.append({
            "sku":   card["data-sku"],
            "name":  card.select_one("h2.name").get_text(strip=True),
            "price": float(re.search(r"[\d.]+", price_text).group()),
            "url":   urljoin(base_url, card.select_one("a.detail-link")["href"]),
        })
    return out


def save_records(target_id: int, cards: list[dict]) -> dict:
    """幂等落库：同一 target 下 url_hash 相同只存一次。

    返回 {"inserted": n, "skipped": m}。commit 在循环外 —— 要么全进去，要么全不进。
    """
    if not cards:
        return {"inserted": 0, "skipped": 0}

    # ⓪ url 必须是绝对地址。相对地址 hash 出来是另一串，url_hash 去重形同虚设
    #    —— 11-03 就是这样重复入库 5 条的。宁可不写，也不写脏数据。
    bad = [
        c.get("url")
        for c in cards
        if not str(c.get("url") or "").startswith(("http://", "https://"))
    ]
    if bad:
        raise ValueError(f"url 不是绝对地址，拒绝入库（会让 url_hash 去重失效）：{bad[:3]}")

    s = SessionLocal()
    inserted = 0
    skipped = 0
    try:
        # ① 先把 hash 全算出来、一次查完库里已有的。
        #    原来在循环里每条 query 一次 = N+1 次往返。
        url_hashes = [hashlib.md5(card["url"].encode()).hexdigest() for card in cards]
        existing = {
            h
            for (h,) in s.query(Record.url_hash)
            .filter(
                Record.target_id == target_id,
                Record.url_hash.in_(url_hashes),
            )
            .all()
        }

        # ② seen 记「本批已经插入」的 hash。只查库不够：autoflush=False 时
        #    刚 add 的对象还没进库，同批第 3 条重复 url 查不出来，会一路 add
        #    到 commit 才撞 uq_target_url，把整批一起回滚掉。
        seen: set[str] = set()

        for card, url_hash in zip(cards, url_hashes):
            if url_hash in existing or url_hash in seen:
                skipped += 1
                continue
            seen.add(url_hash)
            # 表里没有独立列的字段全塞 extra：sku、详情页参数表 spec……
            # extra 是建表时预留的 Text 列，加字段不需要动表结构。
            extra_fields = {
                k: v for k, v in card.items() if k not in _CARD_FIELDS and v is not None
            }
            s.add(
                Record(
                    target_id=target_id,
                    title=card["name"],  # cards 里叫 name，表里叫 title
                    # price 不是必填字段：电影的 field_map 里根本没有这一项。
                    # 写 card["price"] 会 KeyError —— 11-16 拿豆瓣真实站点试才暴露，
                    # mock 站每次都配了 price，所以这个假设藏了一整个月。
                    # 表里是 String(50) 要转字符串；但 str(None) 会得到 "None"
                    # 这个字符串去污染数据，得单独挡掉。
                    price=(
                        str(card.get("price"))
                        if card.get("price") is not None
                        else None
                    ),
                    url=card["url"],
                    url_hash=url_hash,
                    extra=(
                        json.dumps(extra_fields, ensure_ascii=False)
                        if extra_fields
                        else None
                    ),
                )
            )
            inserted += 1

        # ④ 循环外统一提交：5 条一次事务
        s.commit()

    except Exception:
        s.rollback()  # 出错必须回滚，否则半截数据留在库里
        raise  # 别吞异常，让调用方知道失败了
    finally:
        s.close()  # 无论成败，连接都还回池子

    return {"inserted": inserted, "skipped": skipped}


CARD_KEY = "card"
# bs4 的 Element.select_one 只匹配「后代」，匹配不到元素自身：
#   card.select_one("article.card")  -> None
#   card.select_one(":scope")        -> None   （soupsieve 在 Element 上不认 :scope）
# 所以「取卡片自身的属性」只能靠一个自定义约定，@ 不是合法 CSS 起始符，不会撞车。
SELF = "@"
# 对应 records 表的 NOT NULL 列。缺了这张卡根本存不进去，解析阶段就丢掉。
# price 可空（"面议"抽不出数字），所以不在必填里。
REQUIRED_FIELDS = ("name", "url")
# records 表有独立列的字段。其余一律进 extra —— 加字段不用改表结构。
_CARD_FIELDS = ("name", "price", "url")


def _pick(card_tag, spec: dict) -> str | None:
    """按一份字段规格，从一张卡片里取出原始字符串。取不到返回 None（不抛异常）。

    spec:
        sel   必填。CSS 选择器；取 "@" 表示卡片元素自身。
        attr  可选。给了就取该属性值，不给就取标签文本。
    """
    tag = card_tag if spec["sel"] == SELF else card_tag.select_one(spec["sel"])
    if tag is None:
        return None
    attr = spec.get("attr")
    if attr:
        return tag.get(attr)  # 属性不存在返回 None，不会像 tag[attr] 那样抛 KeyError
    return tag.get_text(" ", strip=True)


_MONEY_RE = re.compile(r"[\d.]+")


def _to_money(raw: str | None) -> float | None:
    """'¥499¥599' -> 499.0；'面议' -> None。"""
    if not raw:
        return None
    m = _MONEY_RE.search(raw)
    return float(m.group()) if m is not None else None


# cast 名 -> 转换函数。以后要加 "int"/"date" 只多一行，主循环不用动。
_CASTERS = {"money": _to_money}


def parse_by_selector(html: str, base_url: str, field_map: dict) -> list[dict]:
    """按一份 field_map 从列表页抽卡片，返回 [{"name":.., "price":.., "url":.., ...}]。

    field_map 的 key 就是输出字段名，card 是容器选择器：

        {"card":  "article.card",
         "name":  {"sel": "h2.name"},
         "price": {"sel": "p.price", "cast": "money"},
         "url":   {"sel": "a.detail-link", "attr": "href", "join": "base"},
         "sku":   {"sel": "@", "attr": "data-sku"}}

    三条规则：
        attr      空 → 取标签文本；非空 → 取该属性值
        join:base → 结果为相对地址时过 urljoin 变绝对地址
        cast      → 按 _CASTERS 里的函数转换（目前只有 money）
    """
    card_sel = field_map.get(CARD_KEY)
    if not card_sel or not isinstance(card_sel, str):
        raise ValueError(f"field_map 缺少容器选择器 {CARD_KEY!r}（必须是字符串）")

    # 只查 key 不够：{"name": ""} 能过 key 检查，但下一步 select_one("") 会抛
    # soupsieve.SelectorSyntaxError —— 那不是 ValueError，调用方根本 catch 不到。
    missing = [k for k in REQUIRED_FIELDS if not field_map.get(k)]
    if missing:
        raise ValueError(f"field_map 缺少必填字段 {missing}，实际收到 {list(field_map)}")

    # 先把所有字段规格校验一遍，错在配置上就别等解析到一半才炸
    specs: dict[str, dict] = {}
    for key, spec in field_map.items():
        if key == CARD_KEY:
            continue
        if not isinstance(spec, dict) or not spec.get("sel"):
            raise ValueError(f"字段 {key!r} 必须是带 sel 的 dict，例如 {{'sel': 'h2.name'}}")
        cast = spec.get("cast")
        if cast is not None and cast not in _CASTERS:
            raise ValueError(f"字段 {key!r} 的 cast {cast!r} 不认识，可用 {list(_CASTERS)}")
        specs[key] = spec

    soup = BeautifulSoup(html, "html.parser")
    out = []
    for card_tag in soup.select(card_sel):
        row = {}
        for key, spec in specs.items():
            raw = _pick(card_tag, spec)
            if raw is not None and spec.get("join") == "base":
                raw = urljoin(base_url, raw)
            cast = spec.get("cast")
            row[key] = _CASTERS[cast](raw) if cast is not None else raw

        # name / url 是 NOT NULL 列，空标题或空链接的卡片存进去只是垃圾，跳过
        if not row.get("name") or not row.get("url"):
            continue
        out.append(row)

    return out


def parse_kv_table(ref: str, table_selector: str | None = None) -> dict:
    """把详情页 <th>键</th><td>值</td> 这种参数表抓成一个 dict。

    为什么不能用 field_map？因为**键名本身就是数据**：p1 是"轴体/键位数"，
    p2 是"连接方式/分辨率/续航"，p3 是"面板/刷新率"，p5 是"供电/线长/材质"。
    五个页面只有"商品编号/库存"是共通的，固定字段列表穷举不了。

    所以整张表当一个 dict 返回，调用方塞进 records.extra（Text 列，预留的）。

    一个 tr 里可以有多组 th/td（横向排版的参数表），按相邻的 th→td 配对取。
    同名键保留首次出现的值（多张表出现"库存"时不会互相覆盖）。
    """
    html = get_cache().get(ref)
    if html is None:
        raise ValueError(f"ref={ref} 无效，请重新调用 fetch_page 获取新的 ref")

    soup = BeautifulSoup(html, "html.parser")
    roots = soup.select(table_selector) if table_selector else [soup]

    out: dict[str, str] = {}
    for root in roots:
        for tr in root.select("tr"):
            cells = tr.find_all(["th", "td"])
            for i, cell in enumerate(cells):
                if cell.name != "th" or i + 1 >= len(cells):
                    continue
                nxt = cells[i + 1]
                if nxt.name != "td":  # 纯表头行（th 后面还是 th）直接跳过
                    continue
                key = cell.get_text(" ", strip=True)
                value = nxt.get_text(" ", strip=True)
                if key and value:
                    out.setdefault(key, value)
    return out

def list_targets() -> list[dict]:
    """读出全部采集目标，供 Agent 决定「要抓哪些 URL、用什么选择器」。

    返回的 field_map 已经反序列化成 dict，调用方可以直接喂给 parse_by_selector。
    """
    s = SessionLocal()
    try:
        rows = s.query(Target).order_by(Target.id).all()
        return [
            {
                "id": t.id,
                "url": t.url,
                "field_map": json.loads(t.field_map) if t.field_map else {},
                "need_render": bool(t.need_render),
                "last_status": t.last_status,
            }
            for t in rows
        ]
    finally:
        s.close()


def parse_target(target_id: int, ref: str) -> list[dict]:
    """用库里存的 field_map 解析页面 —— 模型只需要给 target_id 和 ref。

    为什么不让模型传 field_map：它会"复述"而不是"复制"。11-03 实测模型复述时
    丢了 join:base，url 保持相对路径 → url_hash 变了 → 去重被绕过 → 重复入库 5 条。
    base_url 也从库里取，模型这两个参数一次都不碰。
    """
    html = get_cache().get(ref)
    if html is None:
        raise ValueError("ref无效，请重新调用fetch_page获取新的ref")
    s = SessionLocal()
    try:
        t = s.get(Target,target_id)
        if t is None:
            raise ValueError(f"target_id={target_id}不存在")
        if not t.field_map:
            raise ValueError(f"target_id={target_id}未配置field_map,无法解析")
        field_map = json.loads(t.field_map)
        base_url = t.url
    finally:
        s.close()
    return parse_by_selector(html, base_url, field_map)
def count_matches(ref:str,selector:str)->dict:
    """只读探测：某个 CSS 选择器在这个页面上能命中几个元素。

    为什么需要它：
        ref 模式把模型和 HTML 彻底隔开了。parse_target 返回 0 条时，
        模型无法判断是「选择器写错了」还是「页面真的没数据」——它会开始瞎猜。
        本工具只回命中数量，不回 HTML 正文：给诊断能力，不给页面内容。
    """
    html = get_cache().get(ref)
    if html is None:
        raise ValueError(f"ref={ref}无效，请重新调用fetch_page获取新的ref")
    soup = BeautifulSoup(html, "html.parser")
    return {"selector": selector, "matches": len(soup.select(selector))}
def extract_links(ref: str, selector: str | None = None) -> dict:
    html = get_cache().get(ref)
    if html is None:
        raise ValueError(f"ref={ref} 无效，请重新调用fetch_page 获取新的ref")
    base_url = get_cache().get_url(ref)
    soup = BeautifulSoup(html, "html.parser")
    sel = selector or "a[href]"
    tags = soup.select(sel)
    links = []
    for a in tags:
        href = a.get("href") or ""
        if not href or href.startswith("#"):
            continue
        url = urljoin(base_url, href)
        if not url.startswith(("http://", "https://")):
            continue
        links.append(url)
    links = list(dict.fromkeys(links))
    return {"links": links, "total": len(links)}



if __name__ == "__main__":
    # 一份完整的 field_map：四种取法各来一个
    #   name  取文本      price 取文本 + 转数字
    #   url   取属性 href + 拼绝对地址     sku 取卡片自身的属性
    FIELD_MAP = {
        "card":  "article.card",
        "sku":   {"sel": SELF, "attr": "data-sku"},
        "name":  {"sel": "h2.name"},
        "price": {"sel": "p.price", "cast": "money"},
        "url":   {"sel": "a.detail-link", "attr": "href", "join": "base"},
    }

    base = "http://127.0.0.1:8099/"
    # ref 模式（11-07 起）：fetch_page 只回取件码，原文留在服务端缓存里。
    # 自测要拿原文，得自己用 ref 去兑换 —— 这一段当初漏改，返回的 dict 被当成
    # html 喂给 bs4，整个 __main__ 自测从 11-07 起就是死的（跑起来 KeyError）。
    ref = fetch_page(base)["ref"]
    html = get_cache().get(ref)
    assert html, "ref 取不出原文，缓存没存住"

    cards = parse_cards(html, base)
    print(f"[硬编码版] 抓到 {len(cards)} 条")
    for c in cards:
        print(f'{c["sku"]:5} | {c["name"]:24} | {c["price"]:>8.1f} | {c["url"]}')
    assert len(cards) == 5, "必须是 5 条"
    assert all(c["url"].startswith("http") for c in cards), "url 必须绝对地址"
    print("✅ 5/5 验收过")

    rows = parse_by_selector(html, base, FIELD_MAP)
    print(f"[field_map 版] 抓到 {len(rows)} 条")
    for r in rows:
        print(f'{r["sku"]:5} | {r["name"]:24} | {r["price"]:>8.1f} | {r["url"]}')
    assert rows == cards, f"field_map 版必须和硬编码版逐条一致\n{rows}\n!=\n{cards}"
    print("✅ field_map 版与硬编码版逐条一致（属性/文本/money/urljoin 四种取法都对）")

    # ── 详情页：参数表是动态键，field_map 装不下，只能整表抓成 dict ──
    spec = parse_kv_table(fetch_page(base + "detail/p1.html")["ref"])
    print(f"[详情页参数表] p1 抓到 {len(spec)} 组")
    for k, v in spec.items():
        print(f"    {k}: {v}")
    assert len(spec) == 6, f"p1 应该有 6 组参数，实际 {len(spec)}"
    assert spec["商品编号"] == "K87", "商品编号取错了"

    # 五页的键组合各不相同 —— 这就是「固定字段列表穷举不了」的实证
    key_sets = {
        tuple(parse_kv_table(fetch_page(f"{base}detail/p{i}.html")["ref"])) for i in range(1, 6)
    }
    print(f"[动态键] 5 页产出 {len(key_sets)} 种不同的键组合")
    assert len(key_sets) == 5, "五页键组合应该各不相同"
    print("✅ 详情页参数表抽取通过")
    # ── extract_links：链接发现（翻页的地基）──
    r_idx = fetch_page(base)["ref"]
    r_p2  = fetch_page(base + "page2.html")["ref"]
    r_p3  = fetch_page(base + "page3.html")["ref"]

    d = extract_links(r_idx)
    print(f"[链接发现] 首页默认 {d['total']} 条，最后一条 {d['links'][-1]}")
    assert d["total"] == 6, f"首页应有 6 条（5 详情 + 1 下一页），实际 {d['total']}"
    assert all(u.startswith("http://") for u in d["links"]), "出现非绝对地址"
    assert d["links"][-1].endswith("page2.html"), "「下一页」必须排最后（保序才有意义）"

    assert extract_links(r_idx, "a.detail-link")["total"] == 5, "详情链接应为 5 条"
    assert extract_links(r_p2, "a.next")["links"] == [base + "page3.html"], "page2 下一页应是 page3"
    assert extract_links(r_p3, "a.next")["total"] == 0, "最后一页不该有下一页（终止信号）"
    assert extract_links(r_idx)["links"] == d["links"], "同 ref 两次必须同结果"
    print("✅ 链接发现通过（含终止信号）")
