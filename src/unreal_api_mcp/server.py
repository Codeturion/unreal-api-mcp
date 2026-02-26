"""MCP server exposing Unreal Engine API documentation tools."""

from __future__ import annotations

import json
import sqlite3
import sys
from typing import Any

from mcp.server.fastmcp import FastMCP

from . import db, version

# ---------------------------------------------------------------------------
# Server setup
# ---------------------------------------------------------------------------

mcp = FastMCP(
    "unreal-api",
    instructions=(
        "Use these tools to look up accurate Unreal Engine C++ API signatures, "
        "#include paths, and class member details instead of guessing. "
        "Always verify API calls before writing UE C++ or Blueprint code."
    ),
)

_conn: sqlite3.Connection | None = None


def _get_conn() -> sqlite3.Connection:
    """Lazy-init the database connection."""
    global _conn
    if _conn is None:
        db_path = version.ensure_db()
        ver = version.detect_version()
        print(f"unreal-api-mcp: serving UE {ver} ({db_path})", file=sys.stderr)
        _conn = db.get_connection(db_path)
    return _conn


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def _format_record(r: dict[str, Any]) -> str:
    """Format a single API record for display."""
    lines: list[str] = []

    # Header.
    macro = r.get("macro_type", "")
    mtype = r.get("member_type", "")
    label = macro or mtype.upper()
    lines.append(f"[{label}] {r['fqn']}")

    # Module + include.
    if r.get("module"):
        lines.append(f"  Module: {r['module']}")
    if r.get("include_path"):
        lines.append(f"  #include \"{r['include_path']}\"")

    # Parent / base class (for class/struct).
    if mtype in ("class", "struct") and r.get("return_type"):
        lines.append(f"  Inherits: {r['return_type']}")

    # Summary.
    if r.get("summary"):
        lines.append(f"  Summary: {r['summary']}")

    # Parameters (for functions).
    if mtype == "function":
        params = r.get("params_json", "[]")
        if isinstance(params, str):
            params = json.loads(params)
        if params:
            lines.append("  Parameters:")
            for p in params:
                ptype = p.get("type", "")
                desc = p.get("description", "")
                pname = p.get("name", "")
                if ptype and desc:
                    lines.append(f"    - {pname} ({ptype}): {desc}")
                elif ptype:
                    lines.append(f"    - {pname} ({ptype})")
                else:
                    lines.append(f"    - {pname}")
        ret = r.get("return_type", "")
        if ret:
            lines.append(f"  Returns: {ret}")

    # Type (for properties).
    if mtype == "property" and r.get("return_type"):
        lines.append(f"  Type: {r['return_type']}")

    # Enum values.
    if mtype == "enum":
        vals = r.get("params_json", "[]")
        if isinstance(vals, str):
            vals = json.loads(vals)
        if vals and isinstance(vals, list) and vals and "name" in vals[0]:
            names = [v["name"] for v in vals[:15]]
            lines.append(f"  Values: {', '.join(names)}")
            if len(vals) > 15:
                lines.append(f"    ... and {len(vals) - 15} more")

    # Specifiers.
    if r.get("specifiers"):
        lines.append(f"  Specifiers: {r['specifiers']}")

    # Deprecation.
    if r.get("deprecated"):
        hint = r.get("deprecation_hint", "")
        if hint:
            lines.append(f"  DEPRECATED: {hint}")
        else:
            lines.append("  DEPRECATED")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# MCP Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def search_unreal_api(
    query: str,
    n_results: int = 10,
    member_type: str = "",
) -> str:
    """Search the Unreal Engine API by keyword.

    Args:
        query: Search terms (class name, function name, or keyword).
        n_results: Number of results to return (1-20, default 10).
        member_type: Filter by type: "class", "struct", "enum", "function",
                     "property", or "delegate". Leave empty for all.
    """
    conn = _get_conn()
    results = db.search(
        conn,
        query,
        n_results=n_results,
        member_type=member_type or None,
    )
    if not results:
        return f"No results found for '{query}'."
    parts = [_format_record(r) for r in results]
    return f"Found {len(results)} result(s):\n\n" + "\n\n".join(parts)


@mcp.tool()
def get_function_signature(fqn: str) -> str:
    """Get the exact signature of an Unreal Engine function by fully-qualified name.

    Args:
        fqn: Fully-qualified name like "AActor::GetActorLocation" or
             "UGameplayStatics::SpawnActor".
    """
    conn = _get_conn()

    # 1. Exact match.
    r = db.get_by_fqn(conn, fqn)
    if r:
        return _format_record(r)

    # 2. Prefix match (find overloads).
    rows = conn.execute(
        "SELECT * FROM api_records WHERE fqn LIKE ? || '%' AND member_type = 'function' LIMIT 10",
        (fqn,),
    ).fetchall()
    if rows:
        parts = [_format_record(dict(row)) for row in rows]
        return f"Found {len(parts)} match(es):\n\n" + "\n\n".join(parts)

    # 3. FTS fallback.
    results = db.search(conn, fqn, n_results=5, member_type="function")
    if results:
        parts = [_format_record(r) for r in results]
        return (
            f"No exact match for '{fqn}'. Similar functions:\n\n"
            + "\n\n".join(parts)
        )

    return f"No function found matching '{fqn}'."


@mcp.tool()
def get_include_path(name: str) -> str:
    """Get the #include path for an Unreal Engine class, struct, or type.

    Args:
        name: Class or type name (e.g. "AActor", "FHitResult", "ECollisionChannel").
    """
    conn = _get_conn()
    r = db.resolve_include(conn, name)
    if r:
        lines = [
            f"#include \"{r['include_path']}\"",
            f"",
            f"Module: {r['module']}",
            f"Type: [{r['macro_type']}] {r['fqn']}",
        ]
        if r.get("summary"):
            lines.append(f"Summary: {r['summary'][:200]}")
        return "\n".join(lines)
    return f"No include path found for '{name}'."


@mcp.tool()
def get_class_reference(class_name: str) -> str:
    """Get all public members of an Unreal Engine class.

    Args:
        class_name: The class name (e.g. "AActor", "ACharacter", "UGameplayStatics").
    """
    conn = _get_conn()

    # First get the class record itself.
    class_record = db.get_by_fqn(conn, class_name)
    members = db.get_class_members(conn, class_name)

    if not class_record and not members:
        # FTS fallback.
        results = db.search(conn, class_name, n_results=5, member_type="class")
        if results:
            names = [r["fqn"] for r in results]
            return f"Class '{class_name}' not found. Did you mean: {', '.join(names)}?"
        return f"Class '{class_name}' not found."

    parts: list[str] = []
    if class_record:
        parts.append(_format_record(class_record))
        parts.append("")

    # Group members by type.
    grouped: dict[str, list[dict]] = {}
    for m in members:
        grouped.setdefault(m["member_type"], []).append(m)

    for mtype in ("function", "property", "delegate"):
        group = grouped.get(mtype, [])
        if not group:
            continue
        parts.append(f"--- {mtype.upper()}S ({len(group)}) ---")
        for m in group:
            sig = m["fqn"]
            ret = m.get("return_type", "")
            summary = (m.get("summary") or "")[:80]
            spec = m.get("specifiers", "")
            line = f"  {sig}"
            if ret:
                line += f" -> {ret}"
            if summary:
                line += f"  // {summary}"
            parts.append(line)
            if spec:
                parts.append(f"    [{spec[:60]}]")
        parts.append("")

    return "\n".join(parts)


@mcp.tool()
def get_deprecation_warnings(name: str) -> str:
    """Check if an Unreal Engine API is deprecated.

    Args:
        name: API name to check (function, class, property, etc.).
    """
    conn = _get_conn()
    results = db.search_deprecated(conn, name)
    if not results:
        return f"'{name}' is not deprecated (or not found in the database)."
    parts = [_format_record(r) for r in results]
    return f"Found {len(results)} deprecated API(s):\n\n" + "\n\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
