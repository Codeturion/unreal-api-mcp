"""Regex-based parser for Unreal Engine C++ headers.

Extracts UCLASS, USTRUCT, UENUM, UFUNCTION, and UPROPERTY declarations
along with their Doxygen doc comments, specifiers, and deprecation info.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Balanced-paren macro finder
# ---------------------------------------------------------------------------


def _find_macro_occurrences(source: str, macro: str) -> list[tuple[int, int, str]]:
    """Find all occurrences of MACRO(...) handling nested parentheses.

    Returns list of (start, end, specifiers_text) where start/end span the
    full ``MACRO(...)`` token and *specifiers_text* is the content between
    the outermost parentheses.
    """
    results: list[tuple[int, int, str]] = []
    search_start = 0
    pattern = re.compile(rf"\b{macro}\s*\(")
    while True:
        m = pattern.search(source, search_start)
        if not m:
            break
        open_pos = source.index("(", m.start())
        depth = 1
        i = open_pos + 1
        while i < len(source) and depth > 0:
            ch = source[i]
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == '"':
                # Skip string literals.
                i += 1
                while i < len(source) and source[i] != '"':
                    if source[i] == "\\":
                        i += 1
                    i += 1
            i += 1
        if depth == 0:
            specifiers = source[open_pos + 1: i - 1].strip()
            results.append((m.start(), i, specifiers))
        search_start = i
    return results


# ---------------------------------------------------------------------------
# Regex patterns (for the declarations *after* the macro)
# ---------------------------------------------------------------------------

# class [API] ClassName : public Base
_CLASS_DECL = re.compile(
    r"\s*class\s+(?:\w+_API\s+)?(\w+)"
    r"(?:\s*:\s*public\s+([\w:]+))?",
)

# struct [API] StructName : public Base
_STRUCT_DECL = re.compile(
    r"\s*struct\s+(?:\w+_API\s+)?(\w+)"
    r"(?:\s*:\s*public\s+([\w:]+))?",
)

# enum [class] [UE_DEPRECATED(...)] EnumName : type
_ENUM_DECL = re.compile(
    r"\s*enum\s+(?:class\s+)?(?:UE_DEPRECATED\s*\([^)]*\)\s+)?(\w+)"
    r"(?:\s*:\s*(\w+))?",
)

# [keywords] [API] ReturnType FuncName(params)
_FUNC_DECL = re.compile(
    r"\s*((?:(?:virtual|static|FORCEINLINE|explicit|inline)\s+)*)"
    r"(?:\w+_API\s+)?"
    r"((?:(?:virtual|static|FORCEINLINE|explicit|inline)\s+)*)"
    r"(?:class\s+)?"
    r"([\w:*&<>, ]+?)\s+"
    r"(\w+)"
    r"\s*\(([^)]*)\)",
    re.DOTALL,
)

# Type Name [:;=]
_PROP_DECL = re.compile(
    r"\s*(?:\w+_API\s+)?"
    r"([\w:*&<>, ]+?)\s+"
    r"(\w+)"
    r"\s*(?:[:;=])",
    re.DOTALL,
)

# Doxygen comment block immediately preceding a declaration.
_DOC_COMMENT_BLOCK = re.compile(
    r"/\*\*(.+?)\*/",
    re.DOTALL,
)

_DOC_COMMENT_SINGLE = re.compile(
    r"((?:[ \t]*///[^\n]*\n)+)",
)

# UE_DEPRECATED(version, "message")
_UE_DEPRECATED = re.compile(
    r"UE_DEPRECATED\s*\(\s*([\d.]+)\s*,\s*\"([^\"]*)\"\s*\)",
)

# @param Name Description
_PARAM_TAG = re.compile(r"@param\s+(\w+)\s+(.*?)(?=@\w|$)", re.DOTALL)

# @return(s) Description
_RETURN_TAG = re.compile(r"@returns?\s+(.*?)(?=@\w|$)", re.DOTALL)

# DECLARE_DYNAMIC_MULTICAST_DELEGATE_* / DECLARE_DELEGATE_* etc.
_DELEGATE_DECL = re.compile(
    r"(DECLARE_(?:DYNAMIC_MULTICAST_|DYNAMIC_|MULTICAST_)?DELEGATE"
    r"(?:_\w+)?)\s*\("
    r"([^)]+)\)",
)


# ---------------------------------------------------------------------------
# Comment extraction
# ---------------------------------------------------------------------------


def _find_preceding_comment(source: str, pos: int) -> str:
    """Extract the Doxygen comment block immediately before *pos*."""
    # Look backwards from pos for a /** ... */ or /// block.
    before = source[:pos].rstrip()

    # Try block comment: /** ... */
    idx = before.rfind("*/")
    if idx != -1:
        start = before.rfind("/**", 0, idx)
        if start != -1:
            # Make sure there's only whitespace between */ and our declaration
            gap = before[idx + 2:]
            if gap.strip() == "":
                raw = before[start + 3: idx]
                return _clean_comment(raw)

    # Try line comments: ///
    lines: list[str] = []
    for line in reversed(before.split("\n")):
        stripped = line.strip()
        if stripped.startswith("///"):
            lines.append(stripped[3:].strip())
        elif stripped == "":
            continue
        else:
            break
    if lines:
        lines.reverse()
        return _clean_comment("\n".join(lines))

    return ""


def _clean_comment(raw: str) -> str:
    """Strip leading *, whitespace, and normalise a doc comment."""
    lines = []
    for line in raw.split("\n"):
        line = line.strip()
        if line.startswith("*"):
            line = line[1:].strip()
        lines.append(line)
    text = "\n".join(lines).strip()
    # Collapse multiple whitespace but keep paragraph breaks.
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text


def _extract_summary(comment: str) -> str:
    """Extract the summary (first paragraph, before @param etc.)."""
    # Cut at first @param, @return, @see, @note, @deprecated
    m = re.search(r"\n\s*@(?:param|return|see|note|deprecated|warning)", comment)
    summary = comment[: m.start()] if m else comment
    # Take first paragraph.
    paragraphs = summary.split("\n\n")
    return paragraphs[0].strip() if paragraphs else summary.strip()


def _extract_params(comment: str) -> list[dict[str, str]]:
    """Extract @param tags from a doc comment."""
    params = []
    for m in _PARAM_TAG.finditer(comment):
        desc = m.group(2).strip().replace("\n", " ")
        params.append({"name": m.group(1), "description": desc})
    return params


def _extract_return(comment: str) -> str:
    """Extract @return description from a doc comment."""
    m = _RETURN_TAG.search(comment)
    if m:
        return m.group(1).strip().replace("\n", " ")
    return ""


# ---------------------------------------------------------------------------
# Signature parsing helpers
# ---------------------------------------------------------------------------


def _parse_func_params(raw: str) -> list[dict[str, str]]:
    """Parse a C++ parameter list into structured params.

    Returns a list of {name, type} dicts.  Does *not* include descriptions
    (those come from the doc comment).
    """
    raw = raw.strip()
    if not raw or raw == "void":
        return []

    params = []
    depth = 0
    current = []
    for ch in raw:
        if ch in "<(":
            depth += 1
            current.append(ch)
        elif ch in ">)":
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            params.append("".join(current).strip())
            current = []
        else:
            current.append(ch)
    if current:
        params.append("".join(current).strip())

    result = []
    for p in params:
        p = p.strip()
        if not p:
            continue
        # Remove default values.
        eq = _find_default_value_start(p)
        if eq != -1:
            p = p[:eq].strip()
        # Last token is the name, everything before is the type.
        tokens = p.rsplit(None, 1)
        if len(tokens) == 2:
            ptype, pname = tokens
            # Strip leading 'class ' or 'const class ' etc.
            ptype = re.sub(r"\bclass\s+", "", ptype)
            pname = pname.lstrip("*&")
            result.append({"name": pname, "type": ptype})
        elif len(tokens) == 1:
            result.append({"name": "", "type": tokens[0]})
    return result


def _find_default_value_start(param: str) -> int:
    """Find the position of '=' for default value, respecting templates."""
    depth = 0
    for i, ch in enumerate(param):
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        elif ch == "=" and depth == 0:
            return i
    return -1


# ---------------------------------------------------------------------------
# Deprecation
# ---------------------------------------------------------------------------


def _check_deprecation(source: str, pos: int, end: int) -> tuple[bool, str]:
    """Check for UE_DEPRECATED near a declaration."""
    region = source[max(0, pos - 300): end]
    m = _UE_DEPRECATED.search(region)
    if m:
        return True, m.group(2)

    # Also check for DeprecatedFunction in UFUNCTION specifiers.
    spec_region = source[pos: end]
    if "DeprecatedFunction" in spec_region or "DeprecationMessage" in spec_region:
        dm = re.search(r'DeprecationMessage\s*=\s*"([^"]*)"', spec_region)
        hint = dm.group(1) if dm else ""
        return True, hint

    return False, ""


# ---------------------------------------------------------------------------
# Main parser
# ---------------------------------------------------------------------------


def parse_header(
    source: str,
    *,
    module: str = "",
    include_path: str = "",
) -> list[dict[str, Any]]:
    """Parse a single Unreal Engine header file.

    Returns a list of API record dicts ready for ``db.insert_records``.
    """
    records: list[dict[str, Any]] = []

    # --- Build class context map for member FQNs -----------------------
    class_regions = _build_class_regions(source)

    # --- UCLASS ---------------------------------------------------------
    for start, end, specifiers in _find_macro_occurrences(source, "UCLASS"):
        after = source[end:]
        m = _CLASS_DECL.match(after)
        if not m:
            continue
        class_name = m.group(1)
        base_class = m.group(2) or ""
        comment = _find_preceding_comment(source, start)
        summary = _extract_summary(comment)
        deprecated, dep_hint = _check_deprecation(source, start, end + len(m.group(0)))

        records.append({
            "fqn": class_name,
            "module": module,
            "class_name": class_name,
            "member_name": class_name,
            "member_type": "class",
            "summary": summary,
            "params_json": json.dumps([{"name": "Parent", "type": base_class}]) if base_class else "[]",
            "return_type": base_class,
            "include_path": include_path,
            "deprecated": int(deprecated),
            "deprecation_hint": dep_hint,
            "specifiers": specifiers,
            "macro_type": "UCLASS",
        })

    # --- USTRUCT --------------------------------------------------------
    for start, end, specifiers in _find_macro_occurrences(source, "USTRUCT"):
        after = source[end:]
        m = _STRUCT_DECL.match(after)
        if not m:
            continue
        struct_name = m.group(1)
        base = m.group(2) or ""
        comment = _find_preceding_comment(source, start)
        summary = _extract_summary(comment)
        deprecated, dep_hint = _check_deprecation(source, start, end + len(m.group(0)))

        records.append({
            "fqn": struct_name,
            "module": module,
            "class_name": struct_name,
            "member_name": struct_name,
            "member_type": "struct",
            "summary": summary,
            "params_json": json.dumps([{"name": "Parent", "type": base}]) if base else "[]",
            "return_type": base,
            "include_path": include_path,
            "deprecated": int(deprecated),
            "deprecation_hint": dep_hint,
            "specifiers": specifiers,
            "macro_type": "USTRUCT",
        })

    # --- UENUM ----------------------------------------------------------
    for start, end, specifiers in _find_macro_occurrences(source, "UENUM"):
        after = source[end:]
        m = _ENUM_DECL.match(after)
        if not m:
            continue
        enum_name = m.group(1)
        underlying = m.group(2) or "uint8"
        comment = _find_preceding_comment(source, start)
        summary = _extract_summary(comment)
        deprecated, dep_hint = _check_deprecation(source, start, end + len(m.group(0)))

        # Extract enum values.
        enum_body_start = source.find("{", end)
        if enum_body_start != -1:
            enum_body_end = source.find("};", enum_body_start)
            if enum_body_end != -1:
                body = source[enum_body_start + 1: enum_body_end]
                values = _parse_enum_values(body)
            else:
                values = []
        else:
            values = []

        records.append({
            "fqn": enum_name,
            "module": module,
            "class_name": enum_name,
            "member_name": enum_name,
            "member_type": "enum",
            "summary": summary,
            "params_json": json.dumps(values),
            "return_type": underlying,
            "include_path": include_path,
            "deprecated": int(deprecated),
            "deprecation_hint": dep_hint,
            "specifiers": specifiers,
            "macro_type": "UENUM",
        })

    # --- UFUNCTION ------------------------------------------------------
    for start, end, specifiers in _find_macro_occurrences(source, "UFUNCTION"):
        after = source[end:]
        m = _FUNC_DECL.match(after)
        if not m:
            continue
        keywords = ((m.group(1) or "") + (m.group(2) or "")).strip()
        return_type = m.group(3).strip()
        func_name = m.group(4)
        raw_params = m.group(5)

        return_type = re.sub(r"\bclass\s+", "", return_type)

        owner = _find_owner_class(class_regions, start)
        fqn = f"{owner}::{func_name}" if owner else func_name

        comment = _find_preceding_comment(source, start)
        summary = _extract_summary(comment)
        doc_params = _extract_params(comment)
        doc_return = _extract_return(comment)

        sig_params = _parse_func_params(raw_params)
        params = _merge_params(sig_params, doc_params)

        deprecated, dep_hint = _check_deprecation(source, start, end + len(m.group(0)) + 200)

        records.append({
            "fqn": fqn,
            "module": module,
            "class_name": owner,
            "member_name": func_name,
            "member_type": "function",
            "summary": summary,
            "params_json": json.dumps(params),
            "return_type": doc_return if doc_return else return_type,
            "include_path": include_path,
            "deprecated": int(deprecated),
            "deprecation_hint": dep_hint,
            "specifiers": specifiers,
            "macro_type": "UFUNCTION",
        })

    # --- UPROPERTY ------------------------------------------------------
    for start, end, specifiers in _find_macro_occurrences(source, "UPROPERTY"):
        after = source[end:]
        m = _PROP_DECL.match(after)
        if not m:
            continue
        prop_type = m.group(1).strip()
        prop_name = m.group(2)

        prop_type = re.sub(r"\bclass\s+", "", prop_type)

        owner = _find_owner_class(class_regions, start)
        fqn = f"{owner}::{prop_name}" if owner else prop_name

        comment = _find_preceding_comment(source, start)
        summary = _extract_summary(comment)
        deprecated, dep_hint = _check_deprecation(source, start, end + len(m.group(0)) + 100)

        if prop_name.endswith("_DEPRECATED") and not deprecated:
            deprecated = True

        records.append({
            "fqn": fqn,
            "module": module,
            "class_name": owner,
            "member_name": prop_name,
            "member_type": "property",
            "summary": summary,
            "params_json": "[]",
            "return_type": prop_type,
            "include_path": include_path,
            "deprecated": int(deprecated),
            "deprecation_hint": dep_hint,
            "specifiers": specifiers,
            "macro_type": "UPROPERTY",
        })

    # --- DELEGATE declarations ------------------------------------------
    for m in _DELEGATE_DECL.finditer(source):
        macro_name = m.group(1)
        args = m.group(2).strip()
        parts = [p.strip() for p in args.split(",")]
        if not parts:
            continue
        delegate_name = parts[0]
        if " " in delegate_name:
            continue

        owner = _find_owner_class(class_regions, m.start())
        fqn = f"{owner}::{delegate_name}" if owner else delegate_name
        comment = _find_preceding_comment(source, m.start())
        summary = _extract_summary(comment)

        records.append({
            "fqn": fqn,
            "module": module,
            "class_name": owner,
            "member_name": delegate_name,
            "member_type": "delegate",
            "summary": summary,
            "params_json": "[]",
            "return_type": "",
            "include_path": include_path,
            "deprecated": 0,
            "deprecation_hint": "",
            "specifiers": macro_name,
            "macro_type": macro_name,
        })

    return records


# ---------------------------------------------------------------------------
# Class region tracking
# ---------------------------------------------------------------------------


def _build_class_regions(source: str) -> list[tuple[str, int, int]]:
    """Find class/struct bodies and their byte ranges.

    Returns list of (class_name, start, end) sorted by start position.
    Uses balanced-paren matching so nested ``meta=(...)`` won't break it.
    """
    entries: list[tuple[str, int]] = []

    for macro in ("UCLASS", "USTRUCT"):
        for start, end, _ in _find_macro_occurrences(source, macro):
            after = source[end:]
            decl = _CLASS_DECL if macro == "UCLASS" else _STRUCT_DECL
            m = decl.match(after)
            if m:
                entries.append((m.group(1), start))

    entries.sort(key=lambda x: x[1])

    regions: list[tuple[str, int, int]] = []
    for i, (name, start) in enumerate(entries):
        end = entries[i + 1][1] if i + 1 < len(entries) else len(source)
        regions.append((name, start, end))
    return regions


def _find_owner_class(
    regions: list[tuple[str, int, int]], pos: int
) -> str:
    """Find which class/struct owns the declaration at *pos*."""
    for name, start, end in reversed(regions):
        if start <= pos < end:
            return name
    return ""


# ---------------------------------------------------------------------------
# Enum value parsing
# ---------------------------------------------------------------------------


def _parse_enum_values(body: str) -> list[dict[str, str]]:
    """Extract enum values and their comments from an enum body."""
    values = []
    for line in body.split("\n"):
        line = line.strip()
        if not line or line.startswith("//") or line.startswith("#"):
            continue
        # Remove UMETA(...) annotations.
        line = re.sub(r"UMETA\s*\([^)]*\)", "", line)
        # Remove trailing comma.
        line = line.rstrip(",").strip()
        if not line:
            continue
        # Split on = for explicit values.
        parts = line.split("=", 1)
        name = parts[0].strip()
        if not name or not re.match(r"^\w+$", name):
            continue
        values.append({"name": name, "type": "value"})
    return values


# ---------------------------------------------------------------------------
# Param merging
# ---------------------------------------------------------------------------


def _merge_params(
    sig_params: list[dict[str, str]],
    doc_params: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Merge signature params (name + type) with doc params (name + description)."""
    doc_map = {p["name"]: p.get("description", "") for p in doc_params}
    result = []
    for sp in sig_params:
        desc = doc_map.get(sp["name"], "")
        entry: dict[str, str] = {"name": sp["name"], "type": sp.get("type", "")}
        if desc:
            entry["description"] = desc
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# File-level entry point
# ---------------------------------------------------------------------------


def parse_header_file(
    path: Path,
    *,
    module: str = "",
    include_path: str = "",
) -> list[dict[str, Any]]:
    """Read and parse a single header file from disk."""
    source = path.read_text(encoding="utf-8", errors="replace")

    # Quick check: skip files with no reflection macros.
    if not re.search(r"\b(UCLASS|USTRUCT|UENUM|UFUNCTION|UPROPERTY)\s*\(", source):
        return []

    return parse_header(source, module=module, include_path=include_path)
