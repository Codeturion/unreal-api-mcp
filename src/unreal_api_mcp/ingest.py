"""CLI pipeline to build the Unreal Engine API database.

Usage::

    python -m unreal_api_mcp.ingest --unreal-version 5.6
    python -m unreal_api_mcp.ingest --unreal-version 5.5 --unreal-install H:/UE_5.5
"""

from __future__ import annotations

import argparse
import re
import sys
import time
from pathlib import Path

from . import db, header_parser, unreal_paths

_DEFAULT_CACHE = Path.home() / ".unreal-api-mcp"


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Build the Unreal Engine API SQLite database.",
    )
    parser.add_argument(
        "--unreal-version",
        required=True,
        help='UE version to ingest (e.g. "5.5", "5.6").',
    )
    parser.add_argument(
        "--unreal-install",
        default=None,
        help="Override Unreal Engine install path.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output database path (default: ~/.unreal-api-mcp/unreal_docs_{version}.db).",
    )
    args = parser.parse_args(argv)

    version: str = args.unreal_version
    output_path = Path(
        args.output
        or _DEFAULT_CACHE / f"unreal_docs_{version}.db"
    )

    t_total = time.perf_counter()

    # ------------------------------------------------------------------
    # Phase 1: Locate Unreal Engine install
    # ------------------------------------------------------------------
    print(f"[1/3] Locating Unreal Engine {version}...")
    try:
        if args.unreal_install:
            install = Path(args.unreal_install)
            if not unreal_paths._is_valid_install(install):
                print(f"ERROR: {install} does not look like a UE install.", file=sys.stderr)
                sys.exit(1)
        else:
            install = unreal_paths.find_unreal_install(version)
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
    print(f"  Install: {install}")

    # ------------------------------------------------------------------
    # Phase 2: Discover and parse headers
    # ------------------------------------------------------------------
    print(f"[2/3] Discovering headers...")
    t_parse = time.perf_counter()

    header_dirs = unreal_paths.discover_header_dirs(install)
    print(f"  Found {len(header_dirs)} header directories.")

    headers = unreal_paths.collect_headers(header_dirs)
    print(f"  Found {len(headers)} header files (excluding .generated.h).")

    # Quick-filter: only read files likely to contain reflection macros.
    # This is a fast pre-scan using a simple string search.
    all_records: list[dict] = []
    parsed_count = 0
    skipped_count = 0
    errors: list[tuple[str, str]] = []

    for i, (module_name, include_path, file_path) in enumerate(headers):
        if (i + 1) % 2000 == 0:
            print(f"  ... {i + 1}/{len(headers)} files processed ({len(all_records)} records)")

        try:
            records = header_parser.parse_header_file(
                file_path,
                module=module_name,
                include_path=include_path,
            )
            if records:
                all_records.extend(records)
                parsed_count += 1
            else:
                skipped_count += 1
        except Exception as exc:
            errors.append((str(file_path), str(exc)))
            skipped_count += 1

    t_parse_done = time.perf_counter()
    print(f"  Parsed {parsed_count} files, skipped {skipped_count} (no macros).")
    if errors:
        print(f"  {len(errors)} files had parse errors.")
        for path, err in errors[:5]:
            print(f"    {path}: {err}")
    print(f"  Total records: {len(all_records)}")
    print(f"  Parse time: {t_parse_done - t_parse:.1f}s")

    # ------------------------------------------------------------------
    # Phase 3: Write database
    # ------------------------------------------------------------------
    print(f"[3/3] Writing database to {output_path}...")
    t_write = time.perf_counter()

    conn = db.get_connection(output_path)
    db.clear_all(conn)
    inserted = db.insert_records(conn, all_records)
    conn.close()

    t_write_done = time.perf_counter()
    print(f"  Inserted {inserted} records.")
    print(f"  Write time: {t_write_done - t_write:.1f}s")

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------
    t_done = time.perf_counter()
    print(f"\n{'='*50}")
    print(f"Database: {output_path}")
    print(f"Size: {output_path.stat().st_size / 1024 / 1024:.1f} MB")
    print(f"Total time: {t_done - t_total:.1f}s")

    # Record counts by type.
    type_counts: dict[str, int] = {}
    for r in all_records:
        t = r.get("member_type", "?")
        type_counts[t] = type_counts.get(t, 0) + 1
    for t, c in sorted(type_counts.items()):
        print(f"  {t}: {c}")
    print(f"  TOTAL: {len(all_records)}")


if __name__ == "__main__":
    main()
