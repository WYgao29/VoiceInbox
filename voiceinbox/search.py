"""SQLite FTS5 全文搜索（state/search.sqlite）。转写、纠错后的转写、标题、标签、总结都进索引。

分词用 trigram：中文不需要额外分词器，三个字以上走索引；一两个字退回 LIKE（同一张表，数据量小，够快）。
索引随时可以从 transcripts/ 重建，删掉这个文件不丢任何数据。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS docs USING fts5(
  id UNINDEXED,
  recorded_at UNINDEXED,
  duration_s UNINDEXED,
  title,
  tags,
  transcript,
  summary,
  tokenize = 'trigram'
);
"""

FIELDS = ("title", "tags", "transcript", "summary")


@contextmanager
def _connect(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=10)
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


def upsert(path: Path, *, rec_id: str, recorded_at: str | None, duration_s: float | None, title: str, tags: str, transcript: str, summary: str) -> None:
    with _connect(path) as conn:
        conn.execute("DELETE FROM docs WHERE id = ?", (rec_id,))
        conn.execute(
            "INSERT INTO docs (id, recorded_at, duration_s, title, tags, transcript, summary) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (rec_id, recorded_at or "", duration_s or 0, title or "", tags or "", transcript or "", summary or ""),
        )


def remove(path: Path, rec_id: str) -> None:
    with _connect(path) as conn:
        conn.execute("DELETE FROM docs WHERE id = ?", (rec_id,))


def indexed_ids(path: Path) -> set[str]:
    with _connect(path) as conn:
        return {row["id"] for row in conn.execute("SELECT id FROM docs")}


def clear(path: Path) -> None:
    with _connect(path) as conn:
        conn.execute("DELETE FROM docs")


def _terms(query: str) -> list[str]:
    return [t for t in query.replace("\u3000", " ").split() if t][:8]


def _fts_query(terms: list[str]) -> str:
    return " AND ".join('"' + t.replace('"', '""') + '"' for t in terms)


def search(path: Path, query: str, *, date_from: str | None = None, date_to: str | None = None, limit: int = 50) -> list[dict]:
    """返回 [{id, recorded_at, duration_s, title, field, snippet, offset, length, text_len, rank}]。

    date_from / date_to 是 YYYY-MM-DD（含当天），按录音时间过滤。
    offset 是命中词在该字段里的字符位置，界面用它估算音频时间。
    """
    terms = _terms(query)
    if not terms:
        return []
    where = []
    args: list[object] = []
    if date_from:
        where.append("substr(recorded_at, 1, 10) >= ?")
        args.append(date_from)
    if date_to:
        where.append("substr(recorded_at, 1, 10) <= ?")
        args.append(date_to)
    with _connect(path) as conn:
        if all(len(t) >= 3 for t in terms):
            sql = "SELECT *, bm25(docs, 8.0, 4.0, 1.0, 2.0) AS rank FROM docs WHERE docs MATCH ?"
            params: list[object] = [_fts_query(terms)]
        else:
            clauses = []
            params = []
            for term in terms:
                like = "%" + term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
                clauses.append("(" + " OR ".join(f"{f} LIKE ? ESCAPE '\\'" for f in FIELDS) + ")")
                params.extend([like] * len(FIELDS))
            sql = "SELECT *, 0 AS rank FROM docs WHERE " + " AND ".join(clauses)
        if where:
            sql += " AND " + " AND ".join(where)
            params.extend(args)
        sql += " ORDER BY rank, recorded_at DESC LIMIT ?"
        params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [_hit(dict(row), terms) for row in rows]


def _hit(row: dict, terms: list[str], radius: int = 40) -> dict:
    first = terms[0].casefold()
    field, offset = "title", -1
    for name in ("transcript", "summary", "title", "tags"):
        idx = (row.get(name) or "").casefold().find(first)
        if idx >= 0:
            field, offset = name, idx
            break
    text = " ".join((row.get(field) or "").split()) if offset < 0 else (row.get(field) or "")
    if offset < 0:
        snippet = text[: radius * 2]
    else:
        start = max(0, offset - radius)
        end = min(len(text), offset + len(first) + radius)
        snippet = ("…" if start else "") + " ".join(text[start:end].split()) + ("…" if end < len(text) else "")
    return {
        "id": row["id"],
        "recorded_at": row.get("recorded_at") or "",
        "duration_s": float(row.get("duration_s") or 0),
        "title": row.get("title") or "",
        "field": field,
        "snippet": snippet,
        "offset": offset,
        "text_len": len(row.get(field) or ""),
        "rank": row.get("rank") or 0,
    }


def approx_seconds(hit: dict) -> float | None:
    """转写没有时间戳。按命中位置在全文里的比例估算音频时间，界面上标「约」。"""
    if hit.get("field") != "transcript" or hit.get("offset", -1) < 0:
        return None
    duration = float(hit.get("duration_s") or 0)
    total = int(hit.get("text_len") or 0)
    if duration <= 0 or total <= 0:
        return None
    return max(0.0, min(duration, duration * hit["offset"] / total - 2))
