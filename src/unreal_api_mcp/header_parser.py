"""Regex-based parser for Unreal Engine C++ headers.

Extracts UCLASS, USTRUCT, UENUM, UFUNCTION, and UPROPERTY declarations
along with their Doxygen doc comments, specifiers, and deprecation info.
Also supports Slate widget macros: SLATE_BEGIN_ARGS, SLATE_ATTRIBUTE,
SLATE_EVENT, SLATE_ARGUMENT, and SLATE_NAMED_SLOT.
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
# Slate widget macro patterns
# ---------------------------------------------------------------------------

# SLATE_BEGIN_ARGS(ClassName)
_SLATE_BEGIN_ARGS_RE = re.compile(r"\bSLATE_BEGIN_ARGS\s*\(\s*(\w+)\s*\)")

# SLATE_END_ARGS()
_SLATE_END_ARGS_RE = re.compile(r"\bSLATE_END_ARGS\s*\(\s*\)")

# SLATE_ATTRIBUTE(Type, Name)  — attribute that supports TAttribute animation
_SLATE_ATTRIBUTE_RE = re.compile(
    r"\bSLATE_ATTRIBUTE\s*\(\s*([\w:< >*&,]+?)\s*,\s*(\w+)\s*\)"
)

# SLATE_EVENT(DelegateType, Name)  — event callback slot
_SLATE_EVENT_RE = re.compile(
    r"\bSLATE_EVENT\s*\(\s*([\w:< >*&,]+?)\s*,\s*(\w+)\s*\)"
)

# SLATE_ARGUMENT(Type, Name)  — plain constructor argument (not animatable)
_SLATE_ARGUMENT_RE = re.compile(
    r"\bSLATE_ARGUMENT\s*\(\s*([\w:< >*&,]+?)\s*,\s*(\w+)\s*\)"
)

# SLATE_NAMED_SLOT(Type, Name)  — named widget content slot
_SLATE_NAMED_SLOT_RE = re.compile(
    r"\bSLATE_NAMED_SLOT\s*\(\s*([\w:< >*&,]+?)\s*,\s*(\w+)\s*\)"
)

# class [API] SWidgetName : public SBase — Slate class declaration
_SLATE_CLASS_DECL_RE = re.compile(
    r"\bclass\s+(?:\w+_API\s+)?(S\w+)"
    r"(?:\s*:\s*(?:(?:public|protected|private)\s+)?(S[\w:]+))?"
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

    # --- Slate widget classes -------------------------------------------
    records.extend(
        _parse_slate_classes(source, module=module, include_path=include_path)
    )

    return records


# ---------------------------------------------------------------------------
# Slate widget parsing
# ---------------------------------------------------------------------------


def _parse_slate_classes(
    source: str,
    *,
    module: str = "",
    include_path: str = "",
) -> list[dict[str, Any]]:
    """Parse Slate widget classes using SLATE_BEGIN_ARGS and related macros.

    Extracts class records for SWidget subclasses and their attributes,
    events, arguments, and named slots declared via Slate macros.
    """
    records: list[dict[str, Any]] = []

    for begin_m in _SLATE_BEGIN_ARGS_RE.finditer(source):
        class_name = begin_m.group(1)

        # Find the class declaration by scanning backwards
        before = source[: begin_m.start()]
        class_decl_pattern = re.compile(
            rf"\bclass\s+(?:\w+_API\s+)?{re.escape(class_name)}\b"
            rf"(?:\s*:\s*(?:(?:public|protected|private)\s+)?(S[\w:]+))?"
        )
        base_class = ""
        class_start = begin_m.start()
        last_match = None
        for m in class_decl_pattern.finditer(before):
            last_match = m
        if last_match:
            class_start = last_match.start()
            base_class = last_match.group(1) or ""

        comment = _find_preceding_comment(source, class_start)
        summary = _extract_summary(comment)

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
            "deprecated": 0,
            "deprecation_hint": "",
            "specifiers": "",
            "macro_type": "SLATE_CLASS",
        })

        # Bound the args region: from SLATE_BEGIN_ARGS to SLATE_END_ARGS
        end_m = _SLATE_END_ARGS_RE.search(source, begin_m.start())
        args_region_end = end_m.end() if end_m else len(source)
        args_region = source[begin_m.start(): args_region_end]

        # SLATE_ATTRIBUTE — animatable attribute (e.g. Text, Color)
        for attr_m in _SLATE_ATTRIBUTE_RE.finditer(args_region):
            attr_type = attr_m.group(1).strip()
            attr_name = attr_m.group(2)
            attr_comment = _find_preceding_comment(args_region, attr_m.start())
            records.append({
                "fqn": f"{class_name}::{attr_name}",
                "module": module,
                "class_name": class_name,
                "member_name": attr_name,
                "member_type": "property",
                "summary": _extract_summary(attr_comment),
                "params_json": "[]",
                "return_type": f"TAttribute<{attr_type}>",
                "include_path": include_path,
                "deprecated": 0,
                "deprecation_hint": "",
                "specifiers": "SLATE_ATTRIBUTE",
                "macro_type": "SLATE_ATTRIBUTE",
            })

        # SLATE_EVENT — event/callback slot (e.g. OnClicked, OnHovered)
        for evt_m in _SLATE_EVENT_RE.finditer(args_region):
            evt_type = evt_m.group(1).strip()
            evt_name = evt_m.group(2)
            evt_comment = _find_preceding_comment(args_region, evt_m.start())
            records.append({
                "fqn": f"{class_name}::{evt_name}",
                "module": module,
                "class_name": class_name,
                "member_name": evt_name,
                "member_type": "delegate",
                "summary": _extract_summary(evt_comment),
                "params_json": "[]",
                "return_type": evt_type,
                "include_path": include_path,
                "deprecated": 0,
                "deprecation_hint": "",
                "specifiers": "SLATE_EVENT",
                "macro_type": "SLATE_EVENT",
            })

        # SLATE_ARGUMENT — plain constructor argument (e.g. bIsEnabled)
        for arg_m in _SLATE_ARGUMENT_RE.finditer(args_region):
            arg_type = arg_m.group(1).strip()
            arg_name = arg_m.group(2)
            arg_comment = _find_preceding_comment(args_region, arg_m.start())
            records.append({
                "fqn": f"{class_name}::{arg_name}",
                "module": module,
                "class_name": class_name,
                "member_name": arg_name,
                "member_type": "property",
                "summary": _extract_summary(arg_comment),
                "params_json": "[]",
                "return_type": arg_type,
                "include_path": include_path,
                "deprecated": 0,
                "deprecation_hint": "",
                "specifiers": "SLATE_ARGUMENT",
                "macro_type": "SLATE_ARGUMENT",
            })

        # SLATE_NAMED_SLOT — named content slot (e.g. Content, Header)
        for slot_m in _SLATE_NAMED_SLOT_RE.finditer(args_region):
            slot_type = slot_m.group(1).strip()
            slot_name = slot_m.group(2)
            slot_comment = _find_preceding_comment(args_region, slot_m.start())
            records.append({
                "fqn": f"{class_name}::{slot_name}",
                "module": module,
                "class_name": class_name,
                "member_name": slot_name,
                "member_type": "property",
                "summary": _extract_summary(slot_comment),
                "params_json": "[]",
                "return_type": slot_type,
                "include_path": include_path,
                "deprecated": 0,
                "deprecation_hint": "",
                "specifiers": "SLATE_NAMED_SLOT",
                "macro_type": "SLATE_NAMED_SLOT",
            })

    return records


# ---------------------------------------------------------------------------
# Class region tracking
# ---------------------------------------------------------------------------


def _build_class_regions(source: str) -> list[tuple[str, int, int]]:
    """Find class/struct bodies and their byte ranges.

    Returns list of (class_name, start, end) sorted by start position.
    Uses balanced-paren matching so nested ``meta=(...)`` won't break it.
    Also detects Slate widget classes via SLATE_BEGIN_ARGS.
    """
    entries: list[tuple[str, int]] = []

    for macro in ("UCLASS", "USTRUCT"):
        for start, end, _ in _find_macro_occurrences(source, macro):
            after = source[end:]
            decl = _CLASS_DECL if macro == "UCLASS" else _STRUCT_DECL
            m = decl.match(after)
            if m:
                entries.append((m.group(1), start))

    # Detect Slate widget classes from SLATE_BEGIN_ARGS
    seen_slate: set[str] = set()
    for m in _SLATE_BEGIN_ARGS_RE.finditer(source):
        class_name = m.group(1)
        if class_name in seen_slate:
            continue
        seen_slate.add(class_name)
        before = source[: m.start()]
        class_decl = re.compile(
            rf"\bclass\s+(?:\w+_API\s+)?{re.escape(class_name)}\b"
        )
        last = None
        for cm in class_decl.finditer(before):
            last = cm
        entries.append((class_name, last.start() if last else m.start()))

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

    # Quick check: skip files with no reflection or Slate macros.
    if not re.search(
        r"\b(UCLASS|USTRUCT|UENUM|UFUNCTION|UPROPERTY|SLATE_BEGIN_ARGS)\s*\(",
        source,
    ):
        return []

    return parse_header(source, module=module, include_path=include_path)
