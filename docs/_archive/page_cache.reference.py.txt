"""页面缓存 —— ref 模式的服务端那一半。

为什么需要它：
    抓一页 HTML 可能是 48K 字符。如果把它塞进 messages 交给模型，context 立刻爆。
    所以真数据留在服务端，只把一段短字符串 ref（取件码）交给模型，
    模型要看细节时再拿 ref 来换。这个模块就是"取件码 <-> 大块数据"的兑换处。

两个容器是一对，缺一不可：
    _store      ref -> (html, url, expire_at)     书架
    _url_index  url -> ref                        书名卡片盒（查询方向相反）
"""

import threading
import time
import uuid

_CACHE = None
class PageCache:
    def __init__(self, ttl: int = 300, max_size: int = 100) -> None:
        self._store: dict[str, tuple[str, str, float]] = {}
        self._url_index: dict[str, str] = {}
        self._ttl = ttl            # 秒；不落到 self 上就是死参数
        self._max_size = max_size
        # RLock 而不是 Lock：check_url_index 会被 put 调用，两边都要加锁，
        # 同一个线程二次进入必须被允许，否则永久卡死（Lock 不可重入）。
        self._lock = threading.RLock()

    # ---------------- 对外接口 ----------------

    def put(self, html: str, url: str) -> str:
        """存一页，返回 ref。同一 url 已有存活缓存时复用旧 ref，不新增条目。"""
        with self._lock:
            existing = self.check_url_index(url)
            if existing is not None:
                # 命中：ref 不变，只刷新内容与过期时间
                self._store[existing] = (html, url, time.monotonic() + self._ttl)
                return existing

            self._evict()

            ref = "page_" + uuid.uuid4().hex[:8]
            self._store[ref] = (html, url, time.monotonic() + self._ttl)
            self._url_index[url] = ref
            return ref

    def get(self, ref: str) -> str | None:
        """凭 ref 取 HTML。不存在 / 已过期都返回 None，绝不抛异常。"""
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
        """凭 ref 取这页当初的地址。不存在 / 已过期返回 None。

        为什么需要：extract_links 要把页面上的相对 href 拼成绝对地址，
        而 ref 模式只把取件码交给模型，页面地址只有服务端自己知道。
        不新开一个参数让模型传 url —— 11-03 的教训是模型「复述」会丢东西。
        """
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
        """url 对应的、仍然可用的 ref；没有或已失效返回 None。

        "失效"有两种，都要顺手清理：
          1. 悬空索引：_url_index 说有，_store 里已经没了（被容量淘汰）
          2. 已过期：  _store 里有，但 expire_at 已过
        不清理的后果：put 会误判命中，返回一个 get 取不出内容的 ref。
        """
        with self._lock:
            ref = self._url_index.get(url)
            if ref is None:
                return None

            entry = self._store.get(ref)
            if entry is None:
                self._url_index.pop(url, None)
                return None

            _, _, expire_at = entry
            if time.monotonic() > expire_at:
                self._store.pop(ref, None)
                self._url_index.pop(url, None)
                return None

            return ref

    # ---------------- 内部 ----------------

    def _evict(self) -> None:
        """容量满了就删最早进来的那条（FIFO）。调用方必须已持锁。

        不需要额外的队列结构：Python 3.7+ 的 dict 保证插入顺序，
        next(iter(d)) 就是最早插入的 key。
        """
        while len(self._store) >= self._max_size:
            oldest_ref = next(iter(self._store))
            _, old_url, _ = self._store.pop(oldest_ref)
            # 索引必须跟着删，否则留下悬空引用。
            # 加 == 判断是防御：这个 url 可能已经有更新的 ref，别误删新索引。
            if self._url_index.get(old_url) == oldest_ref:
                self._url_index.pop(old_url, None)


def get_cache() -> PageCache:
    global _CACHE
    if _CACHE is None:
        _CACHE = PageCache()
    return _CACHE