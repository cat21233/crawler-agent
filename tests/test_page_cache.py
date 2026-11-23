"""common/page_cache.py 的单元测试。

pytest 的三条约定（记住这三条就够用）：
  1. 文件名必须是 test_*.py 或 *_test.py，pytest 才会收集
  2. 函数名必须以 test_ 开头，否则会被跳过（不报错，静默跳过）
  3. 断言直接用 assert，不需要 unittest 那套 self.assertEqual

为什么写测试而不是"跑一下看输出"：
  手工跑一次只能证明"这一次是对的"。测试是可重复执行的断言，
  下次改动（比如把 FIFO 改成 LRU）跑一遍就知道有没有弄坏别的东西。

怎么判断一条测试有没有效力：
  把实现故意改坏，它必须变红。红了才叫测试，绿了只能叫"没报错"。
"""

import time

from common.page_cache import PageCache


def test_put_then_get_roundtrip():
    """用例 1「存」：put 返回 page_ 开头的 ref，get 取回一模一样的 HTML。"""
    cache = PageCache(ttl=60, max_size=10)
    html = "<html><body>中文内容 ¥499 元</body></html>"

    ref = cache.put(html, "http://example.com/p1")

    assert ref.startswith("page_"), f"ref 必须以 page_ 开头，实际是 {ref!r}"

    got = cache.get(ref)
    assert got is not None, "刚存进去就取不出来了"
    assert got == html, "取回的内容与存入的不一致"

    # 再加长度和首尾切片断言：一旦真出问题，能立刻看出是编码坏了还是被截断了
    assert len(got) == len(html), f"长度不一致：{len(got)} != {len(html)}"
    assert got[:6] == "<html>", f"开头不对：{got[:6]!r}"
    assert got[-7:] == "</html>", f"结尾不对：{got[-7:]!r}"


def test_get_missing_ref_returns_none():
    """用例 2「取」：不存在的 ref 必须返回 None，不能抛异常。"""
    cache = PageCache()
    got = cache.get("page_不存在的ref")
    assert got is None, f"不存在的 ref 应该返回 None，实际返回 {got!r}"


def test_expired_entry_returns_none():
    """用例 3「过期」：TTL 1 秒 → sleep 1.1 秒 → get 必须返回 None。"""
    cache = PageCache(ttl=1, max_size=10)
    html = "<html><body>中文内容 ¥499 元</body></html>"

    ref = cache.put(html, "http://example.com/p1")
    # 前置断言：先证明 put 是好的，否则下面的 None 可能只是"从来没存进去过"
    assert cache.get(ref) == html

    time.sleep(1.1)

    got = cache.get(ref)
    assert got is None, f"过期后应返回 None，实际返回 {got!r}"


def test_get_url_returns_original_url():
    cache = PageCache(ttl=60, max_size=10)
    ref = cache.put("<html>x</html>", "http://example.com/p9")

    assert cache.get_url(ref) == "http://example.com/p9"

def test_put_same_url_reuses_ref():
    cache = PageCache(ttl=60, max_size=10)
    r1 = cache.put('<html>a</html>', "http://example.com/p10")
    r2 = cache.put('<html>b</html>', "http://example.com/p10")

    assert len(cache._store) == 1
    assert cache.get(r1) == '<html>b</html>'
    assert r2 == r1, "同一 url 二次 put 必须返回同一个 ref，否则调用方拿到的是废码"
def test_fifo_evict_drops_oldest():
    cache = PageCache(ttl=60, max_size=2)
    r1 = cache.put('<html>1</html>', "http://example.com/p10")
    r2 = cache.put('<html>2</html>', "http://example.com/p11")
    r3 = cache.put('<html>3</html>', "http://example.com/p12")

    assert cache.get(r1) is None
    assert cache.get(r2) is not None
    assert cache.get(r3) is not None

def test_dangling_index_is_cleaned():
    cache = PageCache(ttl=60, max_size=10)
    ref = cache.put('<html>1</html>', "http://example.com/p10")
    del cache._store[ref]
    assert cache.check_url_index("http://example.com/p10") is None
    assert "http://example.com/p10" not in cache._url_index






















