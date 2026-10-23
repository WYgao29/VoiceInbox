"""转写之后的整理：纠错、路由、总结、Obsidian、搜索、当日摘要。LLM 用假的。"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from voiceinbox import enrich, obsidian, search, vocab
from voiceinbox.llm import LlmError, LlmUnavailable, parse_json_object, strip_think
from voiceinbox.state import State
from tests.conftest import make_cfg


class FakeClient:
    calls: list[str] = []

    def __init__(self, cfg):
        self.cfg = cfg
        self.model = "fake-qwen"

    def prepare(self):
        pass

    def release(self):
        pass

    def chat_json(self, system, user, *, max_tokens=0):
        FakeClient.calls.append(user[:20])
        if "闪念录音" in user:
            return {"title": "买牛奶", "note": "下班买牛奶。", "tags": ["生活"], "people": [], "projects": [], "todos": ["买牛奶"]}
        if "判断这段录音的场景" in user:
            return {"type": "会议", "title": "Sol 项目周会", "tags": ["周会", "#Sol 进度"], "people": ["张三"], "projects": ["Sol"]}
        if "当日摘要" in user:
            return {"overview": "今天开了周会。", "highlights": ["Sol 进度"], "todos": ["发纪要"], "people": ["张三"], "projects": ["Sol"]}
        if "第 " in user and "部分" in user:
            return {"points": ["片段要点"], "decisions": [], "todos": [], "people": [], "projects": []}
        return {
            "overview": "讨论了 Sol 进度。",
            "segments": [{"heading": "进度", "points": ["完成了接口"]}],
            "decisions": ["下周上线"],
            "todos": ["张三 周五前发纪要"],
            "risks": ["测试不够"],
            "mindmap": {"root": "Sol 周会", "children": [{"name": "进度", "children": [{"name": "接口(完成)"}]}]},
        }


def _setup(tmp_path: Path, **over):
    vault = tmp_path / "vault"
    (vault / ".obsidian").mkdir(parents=True)
    params = dict(llm_enabled=True, obsidian_enabled=True, obsidian_vault=vault, digest_enabled=True, flash_max_s=120)
    params.update(over)
    cfg = make_cfg(tmp_path, **params)
    for d in (cfg.transcripts_dir, cfg.state_dir):
        d.mkdir(parents=True, exist_ok=True)
    vocab.ensure_vocab(cfg)
    return cfg, vault


def _add(cfg, state: State, sha: str, text: str, duration: float, when: str):
    rec = state.upsert_discovered(sha256=sha * 64, source="inbox", original_path=f"/tmp/{sha}.m4a", recorded_at=when, duration_s=duration)
    (cfg.transcripts_dir / f"{rec.id}.txt").write_text(text, encoding="utf-8")
    state.advance(rec.id, "transcribed", text_path=str(cfg.transcripts_dir / f"{rec.id}.txt"), archive_path=f"/tmp/arch {sha}.m4a")
    return state.get(rec.id)


def test_full_and_flash_routes_obsidian_idempotent(tmp_path: Path):
    cfg, vault = _setup(tmp_path)
    state = State(cfg.db_path)
    vocab.add_replacement(cfg, "索尔", "Sol")
    long = _add(cfg, state, "a", "嗯今天开索尔周会。完成了接口。", 600, "2026-10-12T10:00:00+08:00")
    short = _add(cfg, state, "b", "嗯那个下班买牛奶", 8, "2026-10-12T09:00:00+08:00")
    box = enrich.ClientBox(cfg, factory=FakeClient)
    stats = enrich.enrich_pending(cfg, state, box=box)
    assert stats.enriched == 2 and stats.llm_done == 2 and stats.failed == 0
    # 原始转写不动，纠错另存
    assert (cfg.transcripts_dir / f"{long.id}.txt").read_text(encoding="utf-8") == "嗯今天开索尔周会。完成了接口。"
    assert enrich.read_corrected(cfg, long.id) == "嗯今天开Sol周会。完成了接口。"
    analysis = enrich.read_analysis(cfg, long.id)
    assert analysis["type"] == "会议" and analysis["title"] == "Sol 项目周会"
    assert analysis["tags"] == ["周会", "Sol进度"]
    note = Path(analysis["obsidian_path"])
    assert note.is_file() and note.parent == vault / "语音备忘录" / "2026" / "10"
    text = note.read_text(encoding="utf-8")
    assert text.startswith("---\nid: \"%s\"" % long.id)
    assert 'people: ["[[张三]]"]' in text and 'project: ["[[Sol]]"]' in text
    assert "## 概要" in text and "- [ ] 张三 周五前发纪要" in text and "mindmap" in text
    assert "> [!quote]- 完整转写" in text and "## 风险与待定" in text
    assert "接口 完成" in text  # mermaid 去掉括号
    # 用户在受管块外写的东西、frontmatter 自加的键都保留
    edited = text.replace('source: "voiceinbox"', 'source: "voiceinbox"\nmine: 1') + "\n我的批注\n"
    note.write_text(edited, encoding="utf-8")
    box2 = enrich.ClientBox(cfg, factory=FakeClient)
    enrich.enrich_record(cfg, state.get(long.id), box2, force_llm=True, template="访谈")
    again = note.read_text(encoding="utf-8")
    assert "我的批注" in again and "mine: 1" in again
    assert again.count("voiceinbox:begin") == 1
    assert enrich.read_analysis(cfg, long.id)["template"] == "访谈"
    assert list((cfg.backup_dir / "obsidian").iterdir())
    # 闪念进每日笔记
    daily = vault / "语音备忘录" / "每日" / "2026-10-12.md"
    body = daily.read_text(encoding="utf-8")
    assert "下班买牛奶。" in body and "- [ ] 买牛奶" in body
    assert body.count(f"voiceinbox:flash {short.id} 0900 begin") == 1
    enrich.enrich_record(cfg, state.get(short.id), box2, force_llm=True)
    assert daily.read_text(encoding="utf-8").count(f"voiceinbox:flash {short.id} 0900 begin") == 1
    # 已完成的不再重复
    assert not enrich.needs_work(cfg, state.get(long.id))
    # 当日摘要
    assert stats.digests in (0, 1)
    path = enrich.run_digest(cfg, state, enrich.ClientBox(cfg, factory=FakeClient), "2026-10-12", force=True)
    digest = path.read_text(encoding="utf-8")
    assert "今天开了周会" in digest and "Sol 项目周会" in digest and "- [ ] 发纪要" in digest
    # 搜索
    hits = search.search(cfg.search_db_path, "Sol周会")
    assert hits and hits[0]["id"] == long.id
    assert search.search(cfg.search_db_path, "牛奶")[0]["id"] == short.id
    assert search.search(cfg.search_db_path, "牛奶", date_from="2026-10-13") == []
    assert search.approx_seconds({"field": "transcript", "offset": 50, "text_len": 100, "duration_s": 600}) == 298


def test_llm_unavailable_keeps_waiting_without_burning_attempts(tmp_path: Path):
    cfg, vault = _setup(tmp_path)
    state = State(cfg.db_path)
    rec = _add(cfg, state, "c", "长录音内容。", 600, "2026-10-11T10:00:00+08:00")

    class Down(FakeClient):
        def prepare(self):
            raise LlmUnavailable("oMLX 没有在运行")

    stats = enrich.enrich_pending(cfg, state, box=enrich.ClientBox(cfg, factory=Down))
    assert stats.waiting_llm == 1
    row = enrich.get_row(cfg, rec.id)
    assert row["attempts"] == 0 and row["llm"] == 0 and row["exported"] == 1
    note = Path(enrich.read_analysis(cfg, rec.id)["obsidian_path"])
    assert 'status: "仅转写"' in note.read_text(encoding="utf-8")
    assert enrich.needs_work(cfg, state.get(rec.id))
    enrich.enrich_pending(cfg, state, box=enrich.ClientBox(cfg, factory=FakeClient))
    assert 'status: "已总结"' in note.read_text(encoding="utf-8")


@pytest.mark.parametrize("duration", [30, 600], ids=["flash", "full"])
@pytest.mark.parametrize("failure", [LlmUnavailable, LlmError])
def test_failed_regeneration_preserves_existing_outputs(tmp_path: Path, monkeypatch, duration, failure):
    cfg, _vault = _setup(tmp_path, digest_enabled=False)
    state = State(cfg.db_path)
    rec = _add(cfg, state, "f", "我们讨论项目，周五发纪要。", duration, "2026-10-23T10:00:00+08:00")
    enrich.enrich_record(cfg, rec, enrich.ClientBox(cfg, factory=FakeClient))
    analysis = enrich.read_analysis(cfg, rec.id)
    note = Path(analysis.get("obsidian_path") or analysis["daily_path"])
    files = [enrich.analysis_path(cfg, rec.id), enrich.corrected_path(cfg, rec.id), note]
    if duration == 600:
        files.append(enrich.summary_path(cfg, rec.id))
    before = {path: path.read_bytes() for path in files}
    row_before = enrich.get_row(cfg, rec.id)
    # 新纠错规则也不能在失败的重新总结中提前替换已有产物。
    vocab.add_replacement(cfg, "讨论", "商量")

    class Broken(FakeClient):
        def prepare(self):
            if failure is LlmUnavailable:
                raise failure("isolated model failure")

        def chat_json(self, *_args, **_kwargs):
            raise failure("isolated model failure")

    box = enrich.ClientBox(cfg, factory=Broken)
    monkeypatch.setattr(enrich, "ClientBox", lambda _cfg: box)
    with pytest.raises(LlmError, match="isolated model failure"):
        enrich.regenerate(cfg, rec.id)
    assert {path: path.read_bytes() for path in files} == before
    assert enrich.get_row(cfg, rec.id) == row_before


def test_reapply_corrections_without_llm(tmp_path: Path):
    cfg, _vault = _setup(tmp_path)
    state = State(cfg.db_path)
    rec = _add(cfg, state, "d", "我们讨论苏尔。", 600, "2026-10-11T10:00:00+08:00")
    enrich.enrich_pending(cfg, state, box=enrich.ClientBox(cfg, factory=FakeClient))
    vocab.add_replacement(cfg, "讨论", "商量")
    assert enrich.reapply_corrections(cfg, [rec.id]) == 1
    assert enrich.read_corrected(cfg, rec.id) == "我们商量苏尔。"
    assert (cfg.transcripts_dir / f"{rec.id}.txt").read_text(encoding="utf-8") == "我们讨论苏尔。"
    assert "讨论了" not in enrich.read_summary(cfg, rec.id)


def test_llm_disabled_still_exports_transcript(tmp_path: Path):
    cfg, vault = _setup(tmp_path, llm_enabled=False)
    state = State(cfg.db_path)
    rec = _add(cfg, state, "e", "随便说点什么。", 30, "2026-10-10T08:00:00+08:00")
    enrich.enrich_pending(cfg, state)
    daily = vault / "语音备忘录" / "每日" / "2026-10-10.md"
    assert "随便说点什么。" in daily.read_text(encoding="utf-8")
    assert not enrich.needs_work(cfg, state.get(rec.id))
    obsidian.remove_flash(cfg, "2026-10-10", rec.id)
    assert rec.id not in daily.read_text(encoding="utf-8")


def test_digest_schedule_and_json_helpers(tmp_path: Path):
    cfg, _ = _setup(tmp_path)
    tz = ZoneInfo("Asia/Shanghai")
    assert enrich.digest_days_due(cfg, datetime(2026, 10, 12, 22, 0, tzinfo=tz)) == ["2026-10-11"]
    assert enrich.digest_days_due(cfg, datetime(2026, 10, 12, 22, 31, tzinfo=tz)) == ["2026-10-11", "2026-10-12"]
    assert parse_json_object('<think>x</think>```json\n{"a": 1}\n```') == {"a": 1}
    assert strip_think("<think>abc</think>好") == "好"
    with pytest.raises(ValueError):
        parse_json_object("没有")


def test_frontmatter_merge_keeps_user_keys():
    entries, body = obsidian.split_frontmatter('---\nid: "x"\nmine:\n  - a\n  - b\ntitle: "旧"\n---\n正文\n')
    out = obsidian.merge_frontmatter(entries, {"id": "x", "title": "新"})
    assert out == '---\nid: "x"\ntitle: "新"\nmine:\n  - a\n  - b\n---\n'
    assert body == "正文\n"
    assert obsidian.safe_filename('a/b:c*?"d') == "a b c d"
