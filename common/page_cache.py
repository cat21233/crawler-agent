"""页面缓存 —— ref 模式的服务端那一半。

为什么需要它：
    抓一页 HTML 可能是 48K 字符。把它塞进 messages 交给模型，context 立刻爆。
    所以真数据留在服务端，只把一段短字符串 ref（取件码）交给模型，
    模型要看细节时再拿 ref 来换。这个模块就是「取件码 <-> 大块数据」的兑换处。

=============================================================================
契约（调用方已经在用这些，签名不能改）
=============================================================================

    PageCache(ttl: int = 300, max_size: int = 100)
        构造。ttl 单位秒，max_size 是条目上限。

    put(html: str, url: str) -> str
        存一页，返回 ref。ref 必须 "page_" 开头 + 8 位十六进制。

    get(ref: str) -> str | None
        凭 ref 取 HTML。不存在 / 已过期 → None。

    get_url(ref: str) -> str | None
        凭 ref 取这页当初的地址。不存在 / 已过期 → None。
        为什么需要：extract_links 要把相对 href 拼成绝对地址，
        而 ref 模式只把取件码交给模型，页面地址只有服务端自己知道。
        （不新开参数让模型传 url —— 11-03 的教训是模型「复述」会丢东西。）

    check_url_index(url: str) -> str | None
        url 对应的、仍然可用的 ref；没有或已失效 → None。

    get_cache() -> PageCache          ★ 模块级函数，进程内单例
        全项目通过 `get_cache().put(...)` / `get_cache().get(ref)` 使用，
        所以它必须返回**同一个实例**，否则 put 进去的 ref 在另一个实例里查不到。

=============================================================================
行为要求（写之前先想清楚，写的时候不要边写边猜）
=============================================================================

    1. TTL 默认 300 秒、容量默认 100 条；两个值都要真的能被构造参数覆盖
       （不落到 self 上的参数是死参数）。
    2. 同一个 url 重复 put → 必须复用旧 ref、刷新内容与过期时间，**不能新增条目**。
       否则反复抓同一页会让容量被人为吃满。
    3. 容量满时淘汰「最早进来的」那条（FIFO，先进先出）。
    4. 任何方法都不许抛异常：取不到、过期、索引悬空，一律返回 None。
       理由：这是工具层内部设施，抛异常会顺着 loop 冒到模型面前。
    5. 线程安全。FastAPI 里同步回调可能并发进来，读也要加锁。
    6. 过期清理要「双向」：不能只清内容不清索引，也不能只清索引不清内容。
       只清一边会留下悬空引用 —— put 会误判命中，返回一个 get 不出内容的 ref。

=============================================================================
你来写
=============================================================================
"""

import threading
import time
import uuid

_CACHE = None


class PageCache:
    def __init__(self, ttl: int = 300, max_size: int = 100) -> None:
        self._store: dict[str, tuple[str, str, float]] = {}
        self._url_index: dict[str, str] = {}
        self._ttl = ttl
        self._max_size = max_size
        self._lock = threading.RLock()


    def put(self, html: str, url: str) -> str:
        with self._lock:
            existing = self.check_url_index(url)
            if existing is not None:
                self._store[existing] = (html, url, time.monotonic()+self._ttl)
                return existing
            self._evict()
            ref = "page_"+uuid.uuid4().hex[:8]
            self._store[ref] = (html, url, time.monotonic()+self._ttl)
            self._url_index[url] = ref
            return ref

    def get(self, ref: str) -> str | None:
        with self._lock:
            entry = self._store.get(ref)
            if entry is None:
                return None
            html, url, expire_at = entry
            if time.monotonic() > expire_at:
                self._store.pop(ref, None)
                self._url_index.pop(url, None)
                return None
            return html

    def get_url(self, ref: str) -> str | None:
        with self._lock:
            entry = self._store.get(ref)
            if entry is None:
                return None
            html, url, expire_at = entry
            if time.monotonic() > expire_at:
                self._store.pop(ref, None)
                self._url_index.pop(url, None)
                return None
            return url

    def check_url_index(self, url: str) -> str | None:
        with self._lock:
            ref = self._url_index.get(url)
            if ref is None:
                return None

            entry = self._store.get(ref)
            if entry is None:
                self._url_index.pop(url, None)
                return None
            _, _, expire = entry
            if time.monotonic() > expire:
                self._store.pop(ref, None)
                self._url_index.pop(url, None)
                return None
            return ref


    def _evict(self) -> None:
        while len(self._store) >= self._max_size:
            oldest_ref = next(iter(self._store))
            _, old_url, _ = self._store.pop(oldest_ref)
            if self._url_index.get(old_url) == oldest_ref:
                self._url_index.pop(old_url, None)


def get_cache() -> PageCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = PageCache()
    return _CACHE
