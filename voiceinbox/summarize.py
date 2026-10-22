"""转写之后的本机 LLM 步骤：先分类，再按场景模板总结；短录音（闪念）只做润色。

模型只输出 JSON，Markdown 由这里按模板拼出来，格式稳定，也方便以后换模板重生成。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from voiceinbox.llm import OmlxClient

SCENES = ("会议", "访谈", "课程", "通话", "随想", "其他")


@dataclass(frozen=True)
class Template:
    name: str
    focus: str
    decisions_label: str = "决策"
    extras: tuple[tuple[str, str, str], ...] = ()


TEMPLATES: dict[str, Template] = {
    "会议": Template(
        "会议",
        "这是一次会议。重点是议题、各方观点、拍板的决定、谁负责什么、截止时间。",
        extras=(("risks", "风险与待定", "还没定下来的问题、风险、需要再确认的事"),),
    ),
    "访谈": Template(
        "访谈",
        "这是一次访谈或对话。重点是受访者的核心观点、经历、论据，以及值得引用的原话。",
        decisions_label="结论",
        extras=(("quotes", "金句", "值得原样引用的话，尽量保留原话"),),
    ),
    "课程": Template(
        "课程",
        "这是一节课或讲座。重点是知识点、概念定义、例子、步骤，以及课后要做的事。",
        decisions_label="结论",
        extras=(("concepts", "关键概念", "术语或概念：一句话解释"),),
    ),
    "通话": Template(
        "通话",
        "这是一通电话。重点是谁打来、为了什么事、双方约定了什么、接下来要跟进什么。",
        decisions_label="约定",
    ),
    "随想": Template(
        "随想",
        "这是自言自语的想法记录。重点是想法本身、背后的理由、可以继续展开的方向。",
        decisions_label="结论",
        extras=(("ideas", "可以展开的方向", "值得继续想、继续做的点"),),
    ),
    "其他": Template("其他", "按内容本身归纳。"),
}

_COMMON_RULES = """你在帮用户整理自己的录音转写。转写来自本机语音识别，可能有错别字、口癖和重复。
规则：
- 只依据转写内容，不编造没有说过的事实、人名、数字和日期。
- 全部用简体中文。人名、项目名、英文专有名词保持原样。
- 去掉「嗯」「啊」「那个」「就是」「然后」这类口头禅和重复。
- 只输出一个 JSON 对象，不要任何解释，不要 Markdown 代码块。"""


def classify_prompt(text: str, *, known_hotwords: list[str]) -> tuple[str, str]:
    words = "、".join(known_hotwords[:50]) or "（无）"
    system = _COMMON_RULES
    user = f"""判断这段录音的场景，并提取元信息。
场景只能从这几个里选一个：{"、".join(SCENES)}。
JSON 字段：
- "type": 场景
- "title": 一句话主题，12 到 24 个字，不要标点结尾
- "tags": 3 到 6 个中文标签，每个 2 到 6 个字，不带 #
- "people": 提到的人名（没有就空数组）
- "projects": 提到的项目、产品或公司名（没有就空数组）
用户的常用专有名词（只用来校对写法）：{words}

转写：
{text}"""
    return system, user


def summary_prompt(text: str, template: Template, meta: dict) -> tuple[str, str]:
    extras = "".join(f'\n- "{key}": 数组，{desc}' for key, _label, desc in template.extras)
    system = _COMMON_RULES
    user = f"""{template.focus}
主题：{meta.get("title") or "（未定）"}
转写没有时间戳，请按内容自然分段。
JSON 字段：
- "overview": 概要，2 到 4 句话
- "segments": 数组，每项 {{"heading": 小标题, "points": [要点, ...]}}，按录音先后顺序，3 到 8 段（很短的录音 1 到 2 段）
- "decisions": 数组，{template.decisions_label}（没有就空数组）
- "todos": 数组，把录音里所有「谁要做什么」都列出来，每项一条可执行的待办，格式「负责人：事情（截止时间）」，看不出负责人或时间就省略那部分；分段要点里提到的任务也要在这里再列一遍（确实没有才是空数组）{extras}
- "mindmap": {{"root": 中心主题, "children": [{{"name": 分支, "children": [{{"name": 子项}}]}}]}}，3 到 6 个分支，最多两层

转写：
{text}"""
    return system, user


def chunk_notes_prompt(text: str, index: int, total: int) -> tuple[str, str]:
    system = _COMMON_RULES
    user = f"""这是一段长录音转写的第 {index}/{total} 部分。先提炼这一部分，后面会合并。
JSON 字段：
- "points": 按先后顺序的要点数组，尽量保留细节、数字和人名
- "decisions": 数组
- "todos": 数组
- "people": 数组
- "projects": 数组

转写片段：
{text}"""
    return system, user


def flash_prompt(text: str, *, known_hotwords: list[str]) -> tuple[str, str]:
    words = "、".join(known_hotwords[:50]) or "（无）"
    system = _COMMON_RULES
    user = f"""这是一条很短的闪念录音。把它整理成一条干净的随手笔记。
- 删掉口头禅、重复、自我纠正，保留原意和语气，不要扩写
- 如果本来就几乎没有内容（只有「嗯」「啊」之类），note 写空字符串
JSON 字段：
- "title": 8 到 20 个字的主题
- "note": 润色后的正文，一到三句
- "tags": 1 到 4 个中文标签，不带 #
- "people": 数组
- "projects": 数组
- "todos": 数组，里面提到要做的事（没有就空数组）
用户的常用专有名词（只用来校对写法）：{words}

转写：
{text}"""
    return system, user


def digest_prompt(day: str, items: list[dict]) -> tuple[str, str]:
    lines = []
    for item in items:
        lines.append(f"- [{item.get('time', '')}] （{item.get('type', '')}）{item.get('title', '')}：{item.get('brief', '')}")
    joined = "\n".join(lines) or "（无）"
    system = _COMMON_RULES
    user = f"""下面是用户 {day} 这一天所有录音的摘要。写一份当日摘要。
JSON 字段：
- "overview": 这一天做了什么、想了什么，2 到 4 句话
- "highlights": 最值得记住的 3 到 6 点
- "todos": 汇总后去重的待办
- "people": 这一天涉及的人
- "projects": 这一天涉及的项目

录音摘要：
{joined}"""
    return system, user


# ---------- 清洗 ----------

def _str_list(value: object, *, limit: int = 20, max_len: int = 200) -> list[str]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        if isinstance(item, dict):
            item = item.get("text") or item.get("name") or item.get("content") or ""
        text = " ".join(str(item).split()).strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append(text[:max_len])
        if len(out) >= limit:
            break
    return out


_TAG_BAD = re.compile(r"[\s#,，、;；:：/\\\[\]{}()<>\"'`|]+")


def clean_tags(value: object) -> list[str]:
    tags = []
    for tag in _str_list(value, limit=8, max_len=20):
        cleaned = _TAG_BAD.sub("", tag)
        if cleaned and cleaned not in tags and not cleaned.isdigit():
            tags.append(cleaned)
    return tags


_NAME_BAD = re.compile(r"[\[\]|#^\\/:*?\"<>\n]+")


def clean_names(value: object) -> list[str]:
    names = []
    for name in _str_list(value, limit=12, max_len=40):
        cleaned = _NAME_BAD.sub(" ", name).strip()
        if cleaned and cleaned not in names:
            names.append(cleaned)
    return names


def clean_title(value: object, fallback: str) -> str:
    text = " ".join(str(value or "").split()).strip().strip("。.!！?？")
    text = re.sub(r"[\n\r]+", " ", text)
    return (text or fallback)[:60]


def normalize_meta(data: dict, fallback_title: str) -> dict:
    scene = str(data.get("type") or "").strip()
    if scene not in SCENES:
        scene = "其他"
    return {
        "type": scene,
        "title": clean_title(data.get("title"), fallback_title),
        "tags": clean_tags(data.get("tags")),
        "people": clean_names(data.get("people")),
        "projects": clean_names(data.get("projects")),
    }


def _mind_children(value: object, depth: int) -> list[dict]:
    if not isinstance(value, list) or depth > 2:
        return []
    out = []
    for item in value[:8]:
        if isinstance(item, str):
            name, kids = item, []
        elif isinstance(item, dict):
            name, kids = str(item.get("name") or item.get("text") or ""), item.get("children") or []
        else:
            continue
        name = " ".join(name.split())[:40]
        if not name:
            continue
        out.append({"name": name, "children": _mind_children(kids, depth + 1)})
    return out


def normalize_summary(data: dict, template: Template, title: str) -> dict:
    segments = []
    for item in data.get("segments") or []:
        if isinstance(item, dict):
            heading = " ".join(str(item.get("heading") or "").split())[:40]
            points = _str_list(item.get("points"), limit=12, max_len=300)
        elif isinstance(item, str):
            heading, points = "", [item]
        else:
            continue
        if heading or points:
            segments.append({"heading": heading or f"第 {len(segments) + 1} 段", "points": points})
    mind = data.get("mindmap") if isinstance(data.get("mindmap"), dict) else {}
    extras = {key: _str_list(data.get(key), limit=12, max_len=300) for key, _label, _desc in template.extras}
    return {
        "overview": " ".join(str(data.get("overview") or "").split())[:1200],
        "segments": segments[:12],
        "decisions": _str_list(data.get("decisions"), limit=15, max_len=300),
        "todos": _str_list(data.get("todos"), limit=20, max_len=300),
        "extras": extras,
        "mindmap": {
            "root": " ".join(str(mind.get("root") or title).split())[:40] or title,
            "children": _mind_children(mind.get("children"), 1),
        },
    }


# ---------- 拼 Markdown ----------

_MERMAID_BAD = re.compile(r"[()\[\]{}<>\"`'#;:|\\]+")


def _mermaid_text(text: str) -> str:
    cleaned = _MERMAID_BAD.sub(" ", text)
    return " ".join(cleaned.split())[:40] or "…"


def render_mindmap(mind: dict) -> str:
    lines = ["```mermaid", "mindmap", f"  root(({_mermaid_text(mind.get('root') or '主题')}))"]

    def walk(nodes: list[dict], depth: int) -> None:
        for node in nodes:
            lines.append("  " * (depth + 1) + _mermaid_text(node.get("name") or ""))
            walk(node.get("children") or [], depth + 1)

    walk(mind.get("children") or [], 1)
    lines.append("```")
    return "\n".join(lines)


def render_summary_md(summary: dict, template_name: str) -> str:
    template = TEMPLATES.get(template_name, TEMPLATES["其他"])
    out: list[str] = []
    out.append("## 概要")
    out.append(summary.get("overview") or "（没有概要）")
    out.append("")
    out.append("## 分段要点")
    out.append("*转写没有时间戳，按内容分段。*")
    for seg in summary.get("segments") or []:
        out.append("")
        out.append(f"### {seg.get('heading') or '要点'}")
        for point in seg.get("points") or []:
            out.append(f"- {point}")
    out.append("")
    out.append(f"## {template.decisions_label}")
    decisions = summary.get("decisions") or []
    out.extend(f"- {item}" for item in decisions) if decisions else out.append("- 无")
    out.append("")
    out.append("## 待办")
    todos = summary.get("todos") or []
    out.extend(f"- [ ] {item}" for item in todos) if todos else out.append("- 无")
    for key, label, _desc in template.extras:
        values = (summary.get("extras") or {}).get(key) or []
        if not values:
            continue
        out.append("")
        out.append(f"## {label}")
        out.extend(f"- {item}" for item in values)
    out.append("")
    out.append("## 思维导图")
    out.append(render_mindmap(summary.get("mindmap") or {}))
    return "\n".join(out).rstrip() + "\n"


# ---------- 调用 ----------

def split_chunks(text: str, size: int) -> list[str]:
    text = text.strip()
    if len(text) <= size:
        return [text]
    pieces = re.split(r"(?<=[。！？!?\n])", text)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        while len(piece) > size:
            if current:
                chunks.append(current)
                current = ""
            chunks.append(piece[:size])
            piece = piece[size:]
        if len(current) + len(piece) > size and current:
            chunks.append(current)
            current = ""
        current += piece
    if current.strip():
        chunks.append(current)
    return chunks


def condense(client: OmlxClient, text: str, size: int) -> tuple[str, dict]:
    """长转写先分段提炼，再合成一份「笔记」给后面的分类和总结。"""
    chunks = split_chunks(text, size)
    if len(chunks) == 1:
        return text, {}
    notes: list[str] = []
    merged: dict[str, list[str]] = {"decisions": [], "todos": [], "people": [], "projects": []}
    for index, chunk in enumerate(chunks, 1):
        system, user = chunk_notes_prompt(chunk, index, len(chunks))
        data = client.chat_json(system, user, max_tokens=3000)
        points = _str_list(data.get("points"), limit=40, max_len=400)
        notes.append(f"【第 {index} 部分】\n" + "\n".join(f"- {p}" for p in points))
        for key in merged:
            merged[key].extend(_str_list(data.get(key), limit=30))
    return "\n\n".join(notes), merged


def summarize_full(client: OmlxClient, text: str, *, hotwords: list[str], template_name: str | None, fallback_title: str) -> dict:
    material, merged = condense(client, text, client.cfg.llm_chunk_chars)
    system, user = classify_prompt(material[: client.cfg.llm_chunk_chars], known_hotwords=hotwords)
    meta = normalize_meta(client.chat_json(system, user, max_tokens=800), fallback_title)
    if merged:
        meta["people"] = clean_names(meta["people"] + merged.get("people", []))
        meta["projects"] = clean_names(meta["projects"] + merged.get("projects", []))
    chosen = template_name if template_name in TEMPLATES else meta["type"]
    template = TEMPLATES[chosen]
    system, user = summary_prompt(material, template, meta)
    summary = normalize_summary(client.chat_json(system, user, max_tokens=4096), template, meta["title"])
    if merged:
        summary["decisions"] = _str_list(summary["decisions"] + merged.get("decisions", []), limit=20)
        summary["todos"] = _str_list(summary["todos"] + merged.get("todos", []), limit=30)
    return {**meta, "template": chosen, "summary": summary, "chunks": len(split_chunks(text, client.cfg.llm_chunk_chars))}


def polish_flash(client: OmlxClient, text: str, *, hotwords: list[str], fallback_title: str) -> dict:
    system, user = flash_prompt(text[: client.cfg.llm_chunk_chars], known_hotwords=hotwords)
    data = client.chat_json(system, user, max_tokens=1200)
    meta = normalize_meta({**data, "type": "随想"}, fallback_title)
    note = " ".join(str(data.get("note") or "").split())[:2000]
    return {**meta, "type": "闪念", "flash": {"note": note, "todos": _str_list(data.get("todos"), limit=10)}}


def summarize_day(client: OmlxClient, day: str, items: list[dict]) -> dict:
    system, user = digest_prompt(day, items)
    data = client.chat_json(system, user, max_tokens=2000)
    return {
        "overview": " ".join(str(data.get("overview") or "").split())[:1500],
        "highlights": _str_list(data.get("highlights"), limit=10, max_len=300),
        "todos": _str_list(data.get("todos"), limit=30, max_len=300),
        "people": clean_names(data.get("people")),
        "projects": clean_names(data.get("projects")),
    }
