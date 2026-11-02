"""5 个工具的 JSON Schema —— 给模型看的说明书。

格式照 llm_service/tool_calling.py 里的 TOOLS，保持一致的 OpenAI function 格式。
description 写「什么时候用」，不写「我是什么」——模型靠它决定调谁。
"""

FETCH_PAGE = {
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": (
            "抓取一个网页，返回 HTML 原文。"
            "需要查看页面内容时使用；如果解析出 0 条数据，"
            "可以换一组选择器再次调用本工具重试。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "完整 URL，必须带 http:// 或 https://",
                },
            },
            "required": ["url"],
        },
    },
}
LIST_TARGETS = {
    "type": 'function',
    "function": {
        "name": "list_targets",
        "description": (
            "列出所有采集目标。每个目标包含url和一套字段选择器 field_map。"
            "说明这个页面改抓哪些字段,怎么抓。"
            "开始抓取前先调用本工具，看清有哪些页面需要抓；"
            "本工具只返回清单，不执行任何抓取。"
        ),
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        }
    }
}
PARSE_BY_SELECTOR = {
    "type": "function",
    "function": {
        "name": "parse_by_selector",
        "description": (
            "按给定的字段规格从 HTML 里解析出商品列表。"
            "通常在 fetch_page 之后调用：把上一步拿到的 html 原样传入，"
            "field_map 从 list_targets 返回的目标里原样复制。"
            "返回一个数组，每个元素是一条商品；返回空数组说明选择器没命中，"
            "应换一组选择器重试而不是直接保存。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "html": {
                    "type": "string",
                    "description": "要解析的 HTML 原文，直接传 fetch_page 的返回值，不要传 URL",
                },
                "base_url": {
                    "type": "string",
                    "description": "该页面自身的完整 URL，用于把 href 里的相对地址补成绝对地址",
                },
                "field_map": {
                    "type": "object",
                    "description": "字段规格，含 card 容器选择器与每个字段的 sel/attr/join/cast；从 list_targets 的返回中原样复制",
                },
            },
            "required": ["base_url", "html", "field_map"],
        },
    },
}

SAVE_RECORDS = {
    "type": "function",
    "function": {
        "name": "save_records",
        "description": (
            "把解析出的商品列表写入数据库。"
            "按 (target_id, url) 去重，已存在的会跳过，重复执行不会产生重复数据。"
            "返回本次新增和跳过的条数。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_id": {
                    "type": "integer",
                    "description": "采集目标的 id，取自 list_targets 返回的 target['id']",
                },
                "cards": {
                    "type": "array",
                    "description": "parse_by_selector 返回的数组，每项是一条约含 name/price/url/sku 的对象",
                    "items": {"type": "object"},
                },
            },
            "required": ["target_id", "cards"],
        },
    },
}

PARSE_KV_TABLE = {
    "type": "function",
    "function": {
        "name": "parse_kv_table",
        "description": (
            "把详情页里的参数表解析成一个键值字典，适合 <th>轴体</th><td>青轴</td> "
            "这种表格：键名本身就带着信息，且不同商品参数项完全不同（键盘有轴体、"
            "显示器有刷新率），无法用固定字段列表穷举。"
            "抓列表页多条商品用 parse_by_selector；抓单个商品的详细参数用本工具。"
            "返回 {\"键\": \"值\"} 形式的字典，可整体塞进记录的 extra 字段。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "html": {
                    "type": "string",
                    "description": "详情页的 HTML 原文，直接传 fetch_page 的返回值，不要传 URL",
                },
                "table_selector": {
                    "type": "string",
                    "description": "限定在哪个表格里找参数，例如 table.spec；不传则扫描整页所有表格",
                },
            },
            "required": ["html"],
        },
    },
}

ALL_TOOLS = [LIST_TARGETS, FETCH_PAGE, PARSE_BY_SELECTOR, PARSE_KV_TABLE, SAVE_RECORDS]
