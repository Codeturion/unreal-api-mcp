"""Version detection and on-demand database download."""

from __future__ import annotations

import os
import re
import sys
import urllib.request
from pathlib import Path

from . import unreal_paths

_CACHE_DIR = Path.home() / ".unreal-api-mcp"
_GITHUB_RELEASE = (
    "https://github.com/Codeturion/unreal-api-mcp/releases/download/db-v1"
)
_SUPPORTED_VERSIONS = ("5.4", "5.5", "5.6", "5.7")
_DEFAULT_VERSION = "5.7"


def detect_version() -> str:
    """Detect the Unreal Engine version to serve.

    Priority:
      1. ``UNREAL_VERSION`` env var
      2. ``UNREAL_PROJECT_PATH`` env var -> ``.uproject`` -> ``EngineAssociation``
      3. Default to latest
    """
    # 1. Explicit env var.
    env_ver = os.environ.get("UNREAL_VERSION", "").strip()
    if env_ver:
        mapped = _map_version(env_ver)
        if mapped:
            return mapped
        print(
            f"WARNING: UNREAL_VERSION={env_ver!r} not recognised, "
            f"falling back to {_DEFAULT_VERSION}.",
            file=sys.stderr,
        )
        return _DEFAULT_VERSION

    # 2. Project file.
    project_path = os.environ.get("UNREAL_PROJECT_PATH", "").strip()
    if project_path:
        raw = unreal_paths.read_uproject_version(project_path)
        if raw:
            mapped = _map_version(raw)
            if mapped:
                return mapped

    # 3. Default.
    return _DEFAULT_VERSION


def _map_version(raw: str) -> str | None:
    """Map a raw version string to a supported version."""
    raw = raw.strip()
    for v in _SUPPORTED_VERSIONS:
        if raw == v or raw.startswith(v + "."):
            return v
    return None


def db_path(version: str | None = None) -> Path:
    """Return the path to the database for *version* (auto-detected if ``None``)."""
    if version is None:
        version = detect_version()
    return _CACHE_DIR / f"unreal_docs_{version}.db"


def ensure_db(version: str | None = None) -> Path:
    """Ensure the database exists, downloading on first run if needed."""
    if version is None:
        version = detect_version()
    path = db_path(version)
    if path.is_file() and path.stat().st_size > 0:
        return path

    # Download from GitHub Release.
    url = f"{_GITHUB_RELEASE}/unreal_docs_{version}.db"
    print(f"Downloading Unreal {version} API database...", file=sys.stderr)
    print(f"  {url}", file=sys.stderr)

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".db.tmp")
    try:
        urllib.request.urlretrieve(url, str(tmp))
        tmp.rename(path)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        raise RuntimeError(
            f"Failed to download database for UE {version}.\n"
            f"URL: {url}\n"
            f"Error: {exc}\n\n"
            f"If you're building databases locally, run:\n"
            f"  python -m unreal_api_mcp.ingest --unreal-version {version}"
        ) from exc

    size_mb = path.stat().st_size / 1024 / 1024
    print(f"  Downloaded {size_mb:.1f} MB -> {path}", file=sys.stderr)
    return path
