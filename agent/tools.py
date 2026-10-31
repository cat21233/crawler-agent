import hashlib
import json
import re
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

from db import SessionLocal
from db_models import Record


def fetch_page(url: str, timeout: float = 10.0) -> str:
    """GET 一个页面，返回 HTML 文本。非 2xx 直接 raise。"""

    r = httpx.get(url=url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.text


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
            s.add(
                Record(
                    target_id=target_id,
                    title=card["name"],  # cards 里叫 name，表里叫 title
                    # 表里是 String(50)，必须转字符串；但 price 可空，
                    # str(None) 会得到 "None" 这个字符串去污染数据，得单独挡掉
                    price=str(card["price"]) if card["price"] is not None else None,
                    url=card["url"],
                    url_hash=url_hash,
                    extra=json.dumps({"sku": card["sku"]}, ensure_ascii=False),
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
    return tag.get_text(strip=True)


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
    html = fetch_page(base)

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
