import re
from collections import defaultdict


def recommendations(materials, insights, themes):
    groups = {
        theme["topic"]: theme["sources"]
        for theme in themes
        if len(theme["sources"]) >= 2
    }
    words = defaultdict(list)
    common = {
        "that",
        "this",
        "with",
        "from",
        "have",
        "were",
        "been",
        "they",
        "their",
        "which",
        "will",
        "more",
        "about",
        "what",
        "when",
        "where",
    }
    for item in insights:
        terms = re.findall(
            r"[A-Za-z][A-Za-z0-9_-]{3,}|[\u3400-\u9fff]+",
            item["quote"][:2000].casefold(),
        )
        for term in dict.fromkeys(terms):
            if term not in common and len(term) <= 32:
                words[term].append(item["id"])
    for term, ids in words.items():
        if len(ids) >= 2:
            groups.setdefault(term, ids)
    grouped = []
    signatures = set()
    for term, ids in sorted(groups.items(), key=lambda pair: -len(pair[1])):
        signature = tuple(ids)
        if signature in signatures:
            continue
        signatures.add(signature)
        grouped.append(
            {
                "key": f"group-{len(grouped) + 1}",
                "title": f"{term}：多份材料里的共同线索",
                "term": term,
                "ids": ids,
                "reason": f"主题「{term}」出现在 {len(ids)} 条材料中",
            }
        )
        if len(grouped) >= 10:
            break
    return grouped + [
        {
            "key": str(item["id"]),
            "title": materials[item["id"] - 1]["title"],
            "ids": [item["id"]],
            "reason": "原句命中分析关键词" if item["matched"] else "有一条可追溯原句",
        }
        for item in insights
    ]
