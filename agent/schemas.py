"""5 个工具的 JSON Schema —— 给模型看的说明书。

格式照 llm_service/tool_calling.py 里的 TOOLS，保持一致的 OpenAI function 格式。
description 写「什么时候用」，不写「我是什么」——模型靠它决定调谁。
"""

FETCH_PAGE = {
    "type": "function",
    "function": {
        "name": "fetch_page",
        "description": (
            "抓取一个网页，把页面内容存到服务端，只返回一个取件码 ref。"
            "需要解析页面内容时，把 ref 传给 parse_target 或 parse_kv_table。"
            "本工具不返回 HTML 原文：页面可能几十万字符，直接塞进上下文会把对话撑爆。"
            "若返回 ok=false 且 retryable=true，表示站点限流（429），"
            "这是临时状况而非配置错误：不要重试、不要改选择器，把结论报告给用户。"

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
PARSE_TARGET = {
    "type": "function",
    "function": {
        "name": "parse_target",
        "description": (
            "按采集目标已保存的配置，从抓好的页面里解析出商品列表。"
            "通常紧跟 fetch_page 之后调用：把 fetch_page 返回的 ref 原样传进来。"
            "字段配置在服务端保存着，本工具自己读，你不需要也不应该传 field_map。"
            "返回一个数组，每个元素是一条商品；返回空数组说明页面结构和配置对不上，"
            "应先重新 fetch_page 确认页面再重试，不要直接保存。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "target_id": {
                    "type": "integer",
                    "description": "采集目标的 id，取自 list_targets 返回的 target['id']",
                },
                "ref": {
                    "type": "string",
                    "description": "fetch_page 返回的取件码，形如 page_xxxxxxxx",
                },
            },
            "required": ["target_id", "ref"],
        },
    },
}
COUNT_MATCHES = {
    "type": "function",
    "function": {
        "name": "count_matches",
        "description": (
            "探测某个 CSS 选择器在指定页面上能命中几个元素，只返回数量，不返回页面内容。"
            "parse_target 返回空数组时用它诊断：先用目标配置里的 card 选择器探一次，"
            "命中 0 说明配置里的选择器已在页面改版后失效。"
            "也可以试几个候选选择器，用命中数量判断哪个才是真正的容器。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "ref": {
                    "type": "string",
                    "description": "fetch_page 返回的取件码，形如 page_xxxxxxxx",
                },
                "selector": {
                    "type": "string",
                    "description": "要探测的 CSS 选择器，例如 article.card",
                },
            },
            "required": ["ref", "selector"],
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
                "ref": {
                    "type": "string",
                    "description": "fetch_page 返回的取件码，形如 page_xxxxxxxx",
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

ALL_TOOLS = [LIST_TARGETS, FETCH_PAGE, PARSE_TARGET, COUNT_MATCHES, PARSE_KV_TABLE, SAVE_RECORDS]
