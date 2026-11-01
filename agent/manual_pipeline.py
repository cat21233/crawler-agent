"""W1 里程碑：不用 LLM，手动把四个工具串成一条完整链路。

这是 Agent 将来要自动完成的事 —— 先用脚本证明「链路本身」是通的，
W2 再让模型决定每一步该调谁。哪一步断了，这里会立刻暴露。

跑法：
    .\\.venv\\Scripts\\python.exe agent/manual_pipeline.py
"""

from agent.tools import fetch_page, list_targets, parse_by_selector, save_records


def main() -> None:
    targets = list_targets()
    if not targets:
        print("targets 表是空的，先插一条 target")
        return

    t = targets[0]
    print(f"[1] list_targets     -> target id={t['id']}  url={t['url']}")
    print(f"    field_map 字段：{list(t['field_map'])}")

    html = fetch_page(t["url"])
    print(f"[2] fetch_page       -> {len(html)} 字符")

    cards = parse_by_selector(html, t["url"], t["field_map"])
    print(f"[3] parse_by_selector -> {len(cards)} 条")
    if cards:
        print(f"    样例：{cards[0]}")

    result = save_records(t["id"], cards)
    print(f"[4] save_records     -> {result}")


if __name__ == "__main__":
    main()
