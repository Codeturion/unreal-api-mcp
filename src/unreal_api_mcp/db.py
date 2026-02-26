"""SQLite + FTS5 database layer for Unreal Engine API records."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS api_records (
    fqn            TEXT PRIMARY KEY,
    module         TEXT NOT NULL DEFAULT '',
    class_name     TEXT NOT NULL DEFAULT '',
    member_name    TEXT NOT NULL DEFAULT '',
    member_type    TEXT NOT NULL DEFAULT '',
    summary        TEXT NOT NULL DEFAULT '',
    params_json    TEXT NOT NULL DEFAULT '[]',
    return_type    TEXT NOT NULL DEFAULT '',
    include_path   TEXT NOT NULL DEFAULT '',
    deprecated     INTEGER NOT NULL DEFAULT 0,
    deprecation_hint TEXT NOT NULL DEFAULT '',
    specifiers     TEXT NOT NULL DEFAULT '',
    macro_type     TEXT NOT NULL DEFAULT ''
);

CREATE VIRTUAL TABLE IF NOT EXISTS api_fts USING fts5(
    fqn,
    class_name,
    member_name,
    summary,
    content=api_records,
    content_rowid=rowid
);

-- Keep FTS in sync with the main table.
CREATE TRIGGER IF NOT EXISTS api_records_ai AFTER INSERT ON api_records BEGIN
    INSERT INTO api_fts(rowid, fqn, class_name, member_name, summary)
    VALUES (new.rowid, new.fqn, new.class_name, new.member_name, new.summary);
END;

CREATE TRIGGER IF NOT EXISTS api_records_ad AFTER DELETE ON api_records BEGIN
    INSERT INTO api_fts(api_fts, rowid, fqn, class_name, member_name, summary)
    VALUES ('delete', old.rowid, old.fqn, old.class_name, old.member_name, old.summary);
END;
"""

# Member‐type values used across the codebase.
MEMBER_TYPES = ("class", "struct", "enum", "function", "property", "delegate")

# Modules considered "core" get a ranking bonus.
_CORE_MODULES = frozenset({
    "Engine", "CoreUObject", "CoreMinimal", "InputCore",
    "UMG", "SlateCore", "Slate", "GameplayTags",
    "GameplayTasks", "GameplayAbilities", "NavigationSystem",
    "AIModule", "PhysicsCore", "Chaos", "Niagara",
    "EnhancedInput", "CommonUI",
})

# ---------------------------------------------------------------------------
# Connection helpers
# ---------------------------------------------------------------------------


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    """Open (or create) a database and ensure the schema exists."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA)
    return conn


def clear_all(conn: sqlite3.Connection) -> None:
    """Drop every record (used before a fresh ingest)."""
    conn.execute("DELETE FROM api_records")
    conn.commit()


# ---------------------------------------------------------------------------
# Insert
# ---------------------------------------------------------------------------


def insert_records(conn: sqlite3.Connection, records: list[dict[str, Any]]) -> int:
    """Bulk‐insert parsed API records. Returns the number inserted."""
    sql = """\
    INSERT OR REPLACE INTO api_records
        (fqn, module, class_name, member_name, member_type,
         summary, params_json, return_type, include_path,
         deprecated, deprecation_hint, specifiers, macro_type)
    VALUES
        (:fqn, :module, :class_name, :member_name, :member_type,
         :summary, :params_json, :return_type, :include_path,
         :deprecated, :deprecation_hint, :specifiers, :macro_type)
    """
    rows: list[dict[str, Any]] = []
    for r in records:
        rows.append({
            "fqn": r["fqn"],
            "module": r.get("module", ""),
            "class_name": r.get("class_name", ""),
            "member_name": r.get("member_name", ""),
            "member_type": r.get("member_type", ""),
            "summary": r.get("summary", ""),
            "params_json": (
                json.dumps(r["params_json"])
                if isinstance(r.get("params_json"), list)
                else r.get("params_json", "[]")
            ),
            "return_type": r.get("return_type", ""),
            "include_path": r.get("include_path", ""),
            "deprecated": int(r.get("deprecated", 0)),
            "deprecation_hint": r.get("deprecation_hint", ""),
            "specifiers": r.get("specifiers", ""),
            "macro_type": r.get("macro_type", ""),
        })
    conn.executemany(sql, rows)
    conn.commit()
    return len(rows)


# ---------------------------------------------------------------------------
# FTS helpers
# ---------------------------------------------------------------------------

_FTS_STRIP = re.compile(r"[\"*():<>{}^\-~|@!]")


def _escape_fts(query: str) -> str:
    """Sanitise a user query for FTS5.

    Colons (common in C++ FQNs like ``AActor::GetActorLocation``) are
    replaced with spaces so FTS treats them as separate tokens.  Special
    FTS5 operators are stripped, and each remaining token is wrapped in
    quotes with a trailing ``*`` for prefix matching.
    """
    q = query.replace("::", " ").replace(".", " ")
    q = _FTS_STRIP.sub(" ", q)
    tokens = q.split()
    return " ".join(f'"{t}"*' for t in tokens if t)


def _rank_adjust(row: sqlite3.Row) -> float:
    """Compute a ranking adjustment on top of BM25.

    Lower (more negative) is better.  Adjustments:
      • Core‐module bonus:  −2 for top‐level core modules, −1 for plugins
      • Namespace depth:    +0.5 per ``::`` separator (favour shallow APIs)
      • Type bonus:         −1 for type‐level entries (class / struct / enum)
    """
    score = 0.0
    module = row["module"]
    if module in _CORE_MODULES:
        score -= 2.0
    fqn: str = row["fqn"]
    depth = fqn.count("::")
    score += depth * 0.5
    if row["member_type"] in ("class", "struct", "enum"):
        score -= 1.0
    return score


# ---------------------------------------------------------------------------
# Query methods
# ---------------------------------------------------------------------------


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    n_results: int = 10,
    member_type: str | None = None,
) -> list[dict[str, Any]]:
    """Full‐text search with BM25 ranking."""
    n_results = min(max(n_results, 1), 20)
    fts_query = _escape_fts(query)
    if not fts_query:
        return []

    # BM25 column weights: fqn(1), class_name(5), member_name(10), summary(1)
    sql = """\
    SELECT r.*, bm25(api_fts, 1.0, 5.0, 10.0, 1.0) AS rank
    FROM api_fts f
    JOIN api_records r ON r.rowid = f.rowid
    WHERE api_fts MATCH :q
    """
    params: dict[str, Any] = {"q": fts_query}
    if member_type:
        sql += " AND r.member_type = :mt"
        params["mt"] = member_type

    # Fetch more than needed so we can re‐rank.
    sql += " ORDER BY rank LIMIT :lim"
    params["lim"] = n_results * 3

    rows = conn.execute(sql, params).fetchall()

    scored = [(dict(row), row["rank"] + _rank_adjust(row)) for row in rows]
    scored.sort(key=lambda x: x[1])
    return [r for r, _ in scored[:n_results]]


def get_by_fqn(conn: sqlite3.Connection, fqn: str) -> dict[str, Any] | None:
    """Exact FQN lookup."""
    row = conn.execute(
        "SELECT * FROM api_records WHERE fqn = ?", (fqn,)
    ).fetchone()
    return dict(row) if row else None


def get_class_members(
    conn: sqlite3.Connection, class_name: str
) -> list[dict[str, Any]]:
    """Return all members belonging to a class."""
    rows = conn.execute(
        "SELECT * FROM api_records WHERE class_name = ? AND member_type != 'class' "
        "AND member_type != 'struct' AND member_type != 'enum' "
        "ORDER BY member_type, member_name",
        (class_name,),
    ).fetchall()
    if not rows:
        # Fallback: try FTS for the class name.
        rows = conn.execute(
            "SELECT r.* FROM api_fts f JOIN api_records r ON r.rowid = f.rowid "
            "WHERE api_fts MATCH :q AND r.class_name != '' "
            "ORDER BY r.member_type, r.member_name LIMIT 50",
            {"q": _escape_fts(class_name)},
        ).fetchall()
    return [dict(r) for r in rows]


def resolve_include(
    conn: sqlite3.Connection, name: str
) -> dict[str, Any] | None:
    """Find the include path for a class or type name."""
    row = conn.execute(
        "SELECT * FROM api_records WHERE class_name = ? AND member_type IN ('class', 'struct', 'enum') LIMIT 1",
        (name,),
    ).fetchone()
    if row:
        return dict(row)
    row = conn.execute(
        "SELECT * FROM api_records WHERE member_name = ? LIMIT 1",
        (name,),
    ).fetchone()
    if row:
        return dict(row)
    # FTS fallback
    fts_q = _escape_fts(name)
    if fts_q:
        row = conn.execute(
            "SELECT r.* FROM api_fts f JOIN api_records r ON r.rowid = f.rowid "
            "WHERE api_fts MATCH :q LIMIT 1",
            {"q": fts_q},
        ).fetchone()
        if row:
            return dict(row)
    return None


def search_deprecated(
    conn: sqlite3.Connection, name: str
) -> list[dict[str, Any]]:
    """Check if an API name is deprecated."""
    # Exact match first.
    rows = conn.execute(
        "SELECT * FROM api_records WHERE (member_name = ? OR class_name = ?) AND deprecated = 1",
        (name, name),
    ).fetchall()
    if rows:
        return [dict(r) for r in rows]
    # FTS fallback.
    fts_q = _escape_fts(name)
    if fts_q:
        rows = conn.execute(
            "SELECT r.* FROM api_fts f JOIN api_records r ON r.rowid = f.rowid "
            "WHERE api_fts MATCH :q AND r.deprecated = 1 LIMIT 10",
            {"q": fts_q},
        ).fetchall()
    return [dict(r) for r in rows]
