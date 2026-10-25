import httpx
import re
from urllib.parse import urljoin
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

if __name__ == "__main__":
    base = "http://127.0.0.1:8099/"
    cards = parse_cards(fetch_page(base), base)
    print(f"抓到 {len(cards)} 条")
    for c in cards:
        print(f'{c["sku"]:5} | {c["name"]:24} | {c["price"]:>8.1f} | {c["url"]}')
    assert len(cards) == 5, "必须是 5 条"
    assert all(c["url"].startswith("http") for c in cards), "url 必须绝对地址"
    print("✅ 5/5 验收过")
