import hashlib
import json
import httpx
import re
from urllib.parse import urljoin
from db import SessionLocal
from db_models import Record
def fetch_page(url: str, timeout: float = 10.0) -> str:
    """GET 一个页面，返回 HTML 文本。非 2xx 直接 raise。"""

    r = httpx.get(url=url, timeout=timeout, follow_redirects=True)
    r.raise_for_status()
    return r.text
from bs4 import BeautifulSoup


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
def save_records(target_id: int,cards: list[dict]) -> dict:
    s = SessionLocal()
    inserted = 0
    skipped = 0
    try:
        for card in cards:
            url_hash = hashlib.md5(card["url"].encode()).hexdigest()
            exists = (
                s.query(Record)
                .filter_by(target_id=target_id,url_hash=url_hash)
                .first()
            )
            if exists is not None:
                skipped += 1
                continue
            s.add(
                Record(
                    target_id=target_id,
                    title=card["name"],  # cards 里叫 name，表里叫 title
                    price=str(card["price"]),  # 表里是 String(50)，必须转字符串
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
REQUIRED_SELECTORS = ("card", "title", "price", "url")
def parse_by_selector(html:str,base_url:str,selectors:dict)->list[dict]:
    missing = [k for k in REQUIRED_SELECTORS if k not in selectors]
    if missing:
        raise ValueError(f"selector缺少必填项{missing},实际收到{list(selectors)}")

    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select(selectors["card"])
    title_sel = selectors["title"]
    price_sel = selectors["price"]
    url_sel = selectors["url"]

    out = []
    for card in cards:
        title_tag = card.select_one(title_sel)   # 单个元素 或 None
        price_tag = card.select_one(price_sel)
        link_tag = card.select_one(url_sel)

        # title / url 在 records 表里是 NOT NULL，缺了这张卡存不进去 → 跳过
        if title_tag is None or link_tag is None:
            continue

        name = title_tag.get_text(strip=True)

        # price 可空：先取文本 "¥499¥599"，抽第一个数字，再转 float
        price = None
        if price_tag is not None:
            m = re.search(r"[\d.]+", price_tag.get_text(strip=True))
            if m is not None:
                price = float(m.group())

        out.append({
            "sku":   card.get("data-sku"),      # .get 不报 KeyError
            "name":  name,
            "price": price,
            "url":   urljoin(base_url, link_tag["href"]),
        })

    return out




if __name__ == "__main__":
    base = "http://127.0.0.1:8099/"
    cards = parse_cards(fetch_page(base), base)
    print(f"抓到 {len(cards)} 条")
    for c in cards:
        print(f'{c["sku"]:5} | {c["name"]:24} | {c["price"]:>8.1f} | {c["url"]}')
    assert len(cards) == 5, "必须是 5 条"
    assert all(c["url"].startswith("http") for c in cards), "url 必须绝对地址"
    print("✅ 5/5 验收过")
