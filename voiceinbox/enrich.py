"""转写之后的几步：纠错替换 → 路由（闪念 / 完整）→ 本机 LLM 分类和总结 → Obsidian → 搜索索引。

原始转写 transcripts/<id>.txt 一字不改。新产物都放在它旁边：
  derived/<id>.corrected.txt   按纠错表替换后的全文
  derived/<id>.analysis.json   类型、标题、标签、人物、项目、总结结构、Obsidian 路径
  derived/<id>.summary.md      按模板拼好的 Markdown 总结（闪念没有）
放进 derived/ 子目录，transcripts/ 下仍然是「每条一个 .txt、一个 .json」。
这些都可以随时重新生成（换模板、改纠错表），不会动原始转写。

调用方必须已经拿着 state/.lock：LLM（Qwen3.6-35B 约 19GB）和 ASR 不会同时跑。
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import traceback
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from voiceinbox import obsidian, search, summarize, vocab
from voiceinbox.config import Config
from voiceinbox.llm import LlmError, LlmUnavailable, OmlxClient
from voiceinbox.lock import FileLock
from voiceinbox.log import LOG, clip
from voiceinbox.state import Record, State
from voiceinbox.timeutil import now as now_in

_SCHEMA = """
CREATE TABLE IF NOT EXISTS enrichment (
  id          TEXT PRIMARY KEY,
  route       TEXT,
  text_sha    TEXT,
  llm         INTEGER NOT NULL DEFAULT 0,
  exported    INTEGER NOT NULL DEFAULT 0,
  attempts    INTEGER NOT NULL DEFAULT 0,
  last_error  TEXT,
  updated_at  TEXT
);
CREATE TABLE IF NOT EXISTS digests (
  day         TEXT PRIMARY KEY,
  signature   TEXT,
  path        TEXT,
  updated_at  TEXT
);
"""


@dataclass
class EnrichStats:
    enriched: int = 0
    llm_done: int = 0
    exported: int = 0
    failed: int = 0
    waiting_llm: int = 0
    digests: int = 0
    errors: list[str] = field(default_factory=list)


@contextmanager
def _db(cfg: Config):
    conn = sqlite3.connect(cfg.db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(_SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_row(cfg: Config, rec_id: str) -> dict | None:
    with _db(cfg) as conn:
        row = conn.execute("SELECT * FROM enrichment WHERE id = ?", (rec_id,)).fetchone()
    return dict(row) if row else None


def _save_row(cfg: Config, rec_id: str, **fields: object) -> None:
    fields["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    with _db(cfg) as conn:
        conn.execute("INSERT OR IGNORE INTO enrichment (id) VALUES (?)", (rec_id,))
        cols = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE enrichment SET {cols} WHERE id = ?", [*fields.values(), rec_id])


# ---------- 文件 ----------

def derived_dir(cfg: Config) -> Path:
    path = cfg.transcripts_dir / "derived"
    path.mkdir(parents=True, exist_ok=True)
    return path


def raw_path(cfg: Config, rec_id: str) -> Path:
    return cfg.transcripts_dir / f"{rec_id}.txt"


def corrected_path(cfg: Config, rec_id: str) -> Path:
    return derived_dir(cfg) / f"{rec_id}.corrected.txt"


def analysis_path(cfg: Config, rec_id: str) -> Path:
    return derived_dir(cfg) / f"{rec_id}.analysis.json"


def summary_path(cfg: Config, rec_id: str) -> Path:
    return derived_dir(cfg) / f"{rec_id}.summary.md"


def _atomic(path: Path, text: str) -> None:
    if path.is_symlink():
        raise RuntimeError(f"拒绝写入符号链接：{path}")
    partial = path.with_name(path.name + ".partial")
    partial.write_text(text, encoding="utf-8")
    os.replace(partial, path)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def read_raw(cfg: Config, rec_id: str) -> str | None:
    path = raw_path(cfg, rec_id)
    try:
        if path.is_symlink() or not path.is_file():
            return None
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def read_corrected(cfg: Config, rec_id: str) -> str | None:
    path = corrected_path(cfg, rec_id)
    try:
        if path.is_file() and not path.is_symlink():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return None


def read_analysis(cfg: Config, rec_id: str) -> dict:
    path = analysis_path(cfg, rec_id)
    try:
        if path.is_file() and not path.is_symlink():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except (OSError, ValueError):
        pass
    return {}


def read_summary(cfg: Config, rec_id: str) -> str:
    path = summary_path(cfg, rec_id)
    try:
        if path.is_file() and not path.is_symlink():
            return path.read_text(encoding="utf-8")
    except OSError:
        pass
    return ""


def _format_duration(seconds: float | None) -> str:
    total = max(0, int(round(seconds or 0)))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}" if hours else f"{minutes}:{secs:02d}"


def route_of(cfg: Config, rec: Record) -> str:
    return "flash" if (rec.duration_s or 0) < cfg.flash_max_s else "full"


def _fallback_title(text: str) -> str:
    flat = " ".join(text.split())
    for mark in "。！？!?":
        if mark in flat:
            flat = flat.split(mark, 1)[0]
            break
    return flat[:24] or "录音"


def _info(cfg: Config, rec: Record, text: str) -> dict:
    audio = rec.archive_path or rec.staged_path or rec.original_path
    return {
        "id": rec.id,
        "recorded_at": rec.recorded_at or "",
        "duration_s": rec.duration_s or 0,
        "duration_label": _format_duration(rec.duration_s),
        "audio_path": audio,
        "char_count": len(text.strip()),
        "title": rec.apple_title or "",
    }


def _search_text(analysis: dict, summary_md: str) -> tuple[str, str, str]:
    title = analysis.get("title") or ""
    tags = " ".join((analysis.get("tags") or []) + (analysis.get("people") or []) + (analysis.get("projects") or []) + [analysis.get("type") or ""])
    flash = (analysis.get("flash") or {}).get("note") or ""
    return title, tags, (summary_md or "") + ("\n" + flash if flash else "")


def index_record(cfg: Config, rec: Record) -> None:
    text = read_corrected(cfg, rec.id) or read_raw(cfg, rec.id) or ""
    analysis = read_analysis(cfg, rec.id)
    title, tags, summary_text = _search_text(analysis, read_summary(cfg, rec.id))
    search.upsert(
        cfg.search_db_path,
        rec_id=rec.id,
        recorded_at=rec.recorded_at,
        duration_s=rec.duration_s,
        title=title or rec.apple_title or "",
        tags=tags,
        transcript=text,
        summary=summary_text,
    )


def reindex(cfg: Config, state: State) -> int:
    search.clear(cfg.search_db_path)
    count = 0
    for rec in state.list_by_status("transcribed"):
        index_record(cfg, rec)
        count += 1
    return count


def sync_index(cfg: Config, state: State) -> int:
    """补上索引里缺的条目。界面启动时调用，便宜。"""
    have = search.indexed_ids(cfg.search_db_path)
    added = 0
    for rec in state.list_by_status("transcribed"):
        if rec.id not in have:
            index_record(cfg, rec)
            added += 1
    return added


# ---------- 单条 ----------

class ClientBox:
    """这一轮最多建一个 LLM 客户端；第一次真要用时才拉起 oMLX。"""

    def __init__(self, cfg: Config, factory=None):
        self.cfg = cfg
        self.factory = factory or OmlxClient
        self.client = None
        self.unavailable: str | None = None

    def get(self):
        if not self.cfg.llm_enabled:
            return None
        if self.unavailable:
            raise LlmUnavailable(self.unavailable)
        if self.client is None:
            client = self.factory(self.cfg)
            try:
                client.prepare()
            except LlmUnavailable as exc:
                self.unavailable = str(exc)
                raise
            self.client = client
        return self.client

    def close(self) -> None:
        if self.client is not None:
            try:
                self.client.release()
            except Exception:
                LOG.exception("释放 LLM 失败")
            self.client = None


def apply_corrections(cfg: Config, rec_id: str, raw: str) -> str:
    rules = vocab.read_replacements(cfg)
    corrected = vocab.apply_replacements(raw, rules)
    path = corrected_path(cfg, rec_id)
    if read_corrected(cfg, rec_id) != corrected:
        _atomic(path, corrected)
    return corrected


def _correct_analysis(analysis: dict, rules: dict[str, str]) -> dict:
    def fix(value):
        if isinstance(value, str):
            return vocab.apply_replacements(value, rules)
        if isinstance(value, list):
            return [fix(v) for v in value]
        if isinstance(value, dict):
            return {k: (v if k in {"obsidian_path", "daily_path", "id", "model", "generated_at", "template", "route", "type"} else fix(v)) for k, v in value.items()}
        return value

    return fix(analysis)


def enrich_record(
    cfg: Config,
    rec: Record,
    box: ClientBox,
    *,
    template: str | None = None,
    force_llm: bool = False,
) -> dict:
    raw = read_raw(cfg, rec.id)
    if raw is None or not raw.strip():
        raise RuntimeError("没有原始转写")
    raw_sha = _sha(raw)
    corrected = apply_corrections(cfg, rec.id, raw)
    route = route_of(cfg, rec)
    previous = read_analysis(cfg, rec.id)
    row = get_row(cfg, rec.id) or {}
    analysis: dict = {
        "id": rec.id,
        "route": route,
        "type": "闪念" if route == "flash" else "其他",
        "title": rec.apple_title if rec.apple_title and not rec.apple_title.startswith("20") else _fallback_title(corrected),
        "tags": [],
        "people": [],
        "projects": [],
        "llm": False,
        "raw_sha": raw_sha,
    }
    # 原始转写没变、上次已经总结过、这次也不要求重做：沿用上次的总结。
    reuse = previous.get("llm") and previous.get("raw_sha") == raw_sha and previous.get("route") == route and not force_llm
    if reuse:
        analysis.update({k: v for k, v in previous.items() if k not in {"obsidian_path", "daily_path"}})
    llm_error = None
    waiting = False
    if not reuse and cfg.llm_enabled:
        try:
            client = box.get()
            hotwords = vocab.read_hotwords(cfg)
            if route == "flash":
                result = summarize.polish_flash(client, corrected, hotwords=hotwords, fallback_title=analysis["title"])
                analysis.update(result)
                analysis["template"] = "闪念"
            else:
                result = summarize.summarize_full(client, corrected, hotwords=hotwords, template_name=template, fallback_title=analysis["title"])
                analysis.update(result)
            analysis["llm"] = True
            analysis["model"] = client.model
            analysis["generated_at"] = now_in(cfg.timezone).isoformat(timespec="seconds")
        except LlmUnavailable as exc:
            waiting = True
            llm_error = str(exc)
            LOG.warning("本机总结暂时用不了 %s：%s", rec.id, exc)
        except LlmError as exc:
            llm_error = str(exc)
            LOG.error("本机总结失败 %s：%s", rec.id, exc)
    summary_md = ""
    if analysis.get("llm") and analysis.get("route") == "full" and analysis.get("summary"):
        summary_md = summarize.render_summary_md(analysis["summary"], analysis.get("template") or analysis.get("type") or "其他")
        if read_summary(cfg, rec.id) != summary_md:
            _atomic(summary_path(cfg, rec.id), summary_md)
    exported = False
    if cfg.obsidian_enabled:
        info = _info(cfg, rec, corrected)
        try:
            if route == "flash":
                if previous.get("obsidian_path"):
                    analysis["obsidian_path"] = previous["obsidian_path"]
                path = obsidian.write_flash(cfg, info, analysis, corrected)
                analysis["daily_path"] = str(path)
            else:
                if previous.get("daily_path"):
                    when = datetime.fromisoformat(rec.recorded_at) if rec.recorded_at else None
                    if when is not None:
                        obsidian.remove_flash(cfg, f"{when:%Y-%m-%d}", rec.id)
                analysis["obsidian_path"] = previous.get("obsidian_path")
                path = obsidian.write_recording_note(cfg, info, analysis, summary_md, corrected)
                analysis["obsidian_path"] = str(path)
            exported = True
        except (obsidian.ObsidianError, OSError) as exc:
            # 总结照样保存；下一轮只补写 Obsidian，不再调用 LLM。
            LOG.error("写 Obsidian 失败 %s：%s", rec.id, exc)
            llm_error = (llm_error + "；" if llm_error else "") + f"Obsidian：{exc}"
    else:
        for key in ("obsidian_path", "daily_path"):
            if previous.get(key):
                analysis[key] = previous[key]
    _atomic(analysis_path(cfg, rec.id), json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
    attempts = int(row.get("attempts") or 0)
    if llm_error and not waiting:
        attempts += 1
    elif analysis.get("llm"):
        attempts = 0
    _save_row(
        cfg,
        rec.id,
        route=route,
        text_sha=raw_sha,
        llm=1 if analysis.get("llm") else 0,
        exported=1 if exported else 0,
        attempts=attempts,
        last_error=llm_error,
    )
    index_record(cfg, rec)
    analysis["_waiting"] = waiting
    analysis["_error"] = llm_error
    return analysis


def needs_work(cfg: Config, rec: Record) -> bool:
    raw = read_raw(cfg, rec.id)
    if raw is None or not raw.strip():
        return False
    row = get_row(cfg, rec.id)
    if row is None or row.get("text_sha") != _sha(raw):
        return True
    if cfg.llm_enabled and not row.get("llm") and int(row.get("attempts") or 0) < cfg.max_attempts:
        return True
    if cfg.obsidian_enabled and not row.get("exported"):
        return True
    return False


def enrich_pending(cfg: Config, state: State, *, box: ClientBox | None = None, limit: int | None = None, ids: list[str] | None = None) -> EnrichStats:
    stats = EnrichStats()
    own = box is None
    box = box or ClientBox(cfg)
    try:
        if ids is None:
            todo = [rec for rec in state.list_by_status("transcribed") if needs_work(cfg, rec)]
        else:
            todo = [rec for rec in (state.get(i) for i in ids) if rec is not None and rec.status == "transcribed"]
        if limit is not None:
            todo = todo[:limit]
        for rec in todo:
            try:
                result = enrich_record(cfg, rec, box)
            except Exception as exc:
                stats.failed += 1
                err = "".join(traceback.format_exception(exc))
                stats.errors.append(f"{rec.id}: {exc}")
                LOG.error("整理失败 %s：%s", rec.id, clip(err, 2000))
                _save_row(cfg, rec.id, last_error=clip(str(exc), 2000))
                continue
            stats.enriched += 1
            if result.get("llm"):
                stats.llm_done += 1
            if result.get("_waiting"):
                stats.waiting_llm += 1
            if result.get("obsidian_path") or result.get("daily_path"):
                stats.exported += 1
        stats.digests += maybe_digest(cfg, state, box)
    finally:
        if own:
            box.close()
    if stats.enriched or stats.failed or stats.digests:
        LOG.info(
            "整理结束：%s 条，总结 %s，写入 Obsidian %s，等 LLM %s，失败 %s，当日摘要 %s",
            stats.enriched, stats.llm_done, stats.exported, stats.waiting_llm, stats.failed, stats.digests,
        )
    return stats


# ---------- 带锁的手动操作（界面和命令行） ----------

class Locked(RuntimeError):
    pass


def _locked(cfg: Config, body):
    with FileLock(cfg.lock_path) as got:
        if not got:
            raise Locked("另一轮转写或总结正在进行")
        return body(State(cfg.db_path))


def regenerate(cfg: Config, rec_id: str, *, template: str | None = None) -> dict:
    """换模板重新总结一条。只改总结和 Obsidian 受管块，不动原始转写。"""
    if template is not None and template not in summarize.TEMPLATES and template != "auto":
        raise ValueError("不认识的模板")
    chosen = None if template in (None, "auto") else template

    def body(state: State) -> dict:
        rec = state.get(rec_id)
        if rec is None:
            raise KeyError(rec_id)
        if rec.status != "transcribed":
            raise ValueError("这条还没有转写完")
        box = ClientBox(cfg)
        try:
            if not cfg.llm_enabled:
                raise ValueError("本机总结没有打开")
            result = enrich_record(cfg, rec, box, template=chosen, force_llm=True)
        finally:
            box.close()
        if result.get("_error"):
            raise LlmError(result["_error"])
        return result

    return _locked(cfg, body)


def reapply_corrections(cfg: Config, rec_ids: list[str] | None = None) -> int:
    """纠错表改了以后：重写纠错后的全文、把总结里的错词一并替换、更新 Obsidian。不调用 LLM。"""

    def body(state: State) -> int:
        rules = vocab.read_replacements(cfg)
        recs = [state.get(i) for i in rec_ids] if rec_ids else state.list_by_status("transcribed")
        count = 0
        for rec in recs:
            if rec is None or rec.status != "transcribed":
                continue
            raw = read_raw(cfg, rec.id)
            if raw is None:
                continue
            corrected = apply_corrections(cfg, rec.id, raw)
            analysis = read_analysis(cfg, rec.id)
            if analysis:
                analysis = _correct_analysis(analysis, rules)
                _atomic(analysis_path(cfg, rec.id), json.dumps(analysis, ensure_ascii=False, indent=2) + "\n")
                summary_md = ""
                if analysis.get("llm") and analysis.get("summary") and analysis.get("route") == "full":
                    summary_md = summarize.render_summary_md(analysis["summary"], analysis.get("template") or "其他")
                    _atomic(summary_path(cfg, rec.id), summary_md)
                if cfg.obsidian_enabled:
                    info = _info(cfg, rec, corrected)
                    if analysis.get("route") == "flash":
                        obsidian.write_flash(cfg, info, analysis, corrected)
                    else:
                        obsidian.write_recording_note(cfg, info, analysis, summary_md, corrected)
            index_record(cfg, rec)
            count += 1
        return count

    return _locked(cfg, body)


# ---------- 当日摘要 ----------

def _day_of(rec: Record) -> str | None:
    if not rec.recorded_at:
        return None
    try:
        return datetime.fromisoformat(rec.recorded_at).strftime("%Y-%m-%d")
    except ValueError:
        return None


def day_items(cfg: Config, state: State, day: str) -> list[dict]:
    items = []
    folder = cfg.obsidian_folder.strip().strip("/")
    for rec in sorted(state.list_by_status("transcribed"), key=lambda r: r.recorded_at or ""):
        if _day_of(rec) != day:
            continue
        analysis = read_analysis(cfg, rec.id)
        if not analysis:
            continue
        when = datetime.fromisoformat(rec.recorded_at)  # type: ignore[arg-type]
        brief = ((analysis.get("summary") or {}).get("overview") or (analysis.get("flash") or {}).get("note") or "")
        if not brief:
            brief = (read_corrected(cfg, rec.id) or "")[:200]
        todos = ((analysis.get("summary") or {}).get("todos") or []) + ((analysis.get("flash") or {}).get("todos") or [])
        if todos:
            brief = brief + " 待办：" + "；".join(todos[:10])
        link = None
        if analysis.get("obsidian_path") and cfg.obsidian_vault:
            try:
                link = obsidian.vault_link(cfg, Path(analysis["obsidian_path"]))
            except (ValueError, OSError):
                link = None
        elif analysis.get("route") == "flash":
            link = f"{folder}/{obsidian.DAILY_DIR}/{day}"
        items.append(
            {
                "id": rec.id,
                "time": f"{when:%H:%M}",
                "type": analysis.get("type") or "",
                "title": analysis.get("title") or "",
                "brief": " ".join(brief.split())[:800],
                "duration_label": _format_duration(rec.duration_s),
                "note_link": link,
                "sha": analysis.get("raw_sha") or "",
                "llm": bool(analysis.get("llm")),
            }
        )
    return items


def _signature(items: list[dict]) -> str:
    return _sha("|".join(f"{i['id']}:{i['sha']}:{int(i['llm'])}:{i['title']}" for i in items))


def run_digest(cfg: Config, state: State, box: ClientBox, day: str, *, force: bool = False) -> Path | None:
    items = day_items(cfg, state, day)
    if not items:
        return None
    signature = _signature(items)
    with _db(cfg) as conn:
        row = conn.execute("SELECT * FROM digests WHERE day = ?", (day,)).fetchone()
    if row and row["signature"] == signature and not force:
        return None
    client = box.get()
    if client is None:
        return None
    digest = summarize.summarize_day(client, day, items)
    path = obsidian.write_digest(cfg, day, digest, items)
    with _db(cfg) as conn:
        conn.execute(
            "INSERT INTO digests (day, signature, path, updated_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(day) DO UPDATE SET signature = excluded.signature, path = excluded.path, updated_at = excluded.updated_at",
            (day, signature, str(path), datetime.now().astimezone().isoformat(timespec="seconds")),
        )
    LOG.info("当日摘要已写入 %s", path)
    return path


def digest_days_due(cfg: Config, moment: datetime) -> list[str]:
    hh, mm = (int(x) for x in cfg.digest_time.split(":"))
    days = [(moment - timedelta(days=1)).strftime("%Y-%m-%d")]
    if (moment.hour, moment.minute) >= (hh, mm):
        days.append(moment.strftime("%Y-%m-%d"))
    return days


def maybe_digest(cfg: Config, state: State, box: ClientBox) -> int:
    """每天 digest_time（默认 22:30）之后生成当天的摘要；跨天时补上前一天（只在内容有变化时）。"""
    if not (cfg.digest_enabled and cfg.llm_enabled and cfg.obsidian_enabled):
        return 0
    done = 0
    moment = now_in(cfg.timezone)
    for day in digest_days_due(cfg, moment):
        try:
            if run_digest(cfg, state, box, day):
                done += 1
        except LlmUnavailable as exc:
            LOG.warning("当日摘要暂缓 %s：%s", day, exc)
            break
        except Exception:
            LOG.exception("当日摘要失败 %s", day)
    return done


def digest_now(cfg: Config, day: str) -> Path | None:
    def body(state: State):
        box = ClientBox(cfg)
        try:
            return run_digest(cfg, state, box, day, force=True)
        finally:
            box.close()

    return _locked(cfg, body)
