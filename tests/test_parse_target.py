"""parse_target 的行为契约。

这个函数最容易「静默失败」：不抛异常，只是返回 [] 或少几个字段。
11-03 那 5 条重复入库就是 join:base 丢了、url 变相对路径导致的。
所以这里的每条断言都对应一个真实踩过的坑。
"""
import pytest

from agent.tools import parse_target
from common.page_cache import get_cache
from tests.conftest import GOOD_FM, PROJECT_ROOT

HTML = (PROJECT_ROOT / "mock_site" / "index.html").read_text(encoding="utf-8")


def test_parse_target_ok(make_target):
    """正常配置解析 mock 首页：5 条，url 全是绝对地址。"""
    tid = make_target(GOOD_FM)
    ref = get_cache().put(HTML, "http://test.local/")

    rows = parse_target(tid, ref)

    assert len(rows) == 5
    # 这一条是 11-03 事故的回归测试：join:base 一丢，url 变相对路径，这里立刻红
    assert all(r["url"].startswith("http://test.local/") for r in rows)
    assert {"sku", "name", "price", "url"} <= set(rows[0])


def test_parse_target_bad_ref(make_target):
    """ref 不存在：报错必须带「重新」，否则模型不知道该重新抓页面。"""
    tid = make_target(GOOD_FM)

    with pytest.raises(ValueError, match="重新"):
        parse_target(tid, "page_deadbeef")


def test_parse_target_missing_target(make_target):
    """target_id 不存在：明确报不存在，不是空数组。"""
    make_target(GOOD_FM)

    with pytest.raises(ValueError, match="不存在"):
        parse_target(999999, get_cache().put(HTML, "http://test.local/"))


def test_parse_target_no_field_map(make_target):
    """没配 field_map：报错要提到 field_map，让调用方知道是配置缺失。"""
    tid = make_target(None)

    with pytest.raises(ValueError, match="field_map"):
        parse_target(tid, get_cache().put(HTML, "http://test.local/"))


def test_parse_target_bad_selector_returns_empty(make_target):
    """选择器失效：返回空数组，不抛异常。

    这条锁定的是「当前设计」——解析不出东西不等于程序出错。
    哪天有人改成抛异常，这个测试会提醒他：那是个决定，不是重构。
    """
    tid = make_target({**GOOD_FM, "card": "article.nonexistent"})

    rows = parse_target(tid, get_cache().put(HTML, "http://test.local/"))

    assert rows == []
