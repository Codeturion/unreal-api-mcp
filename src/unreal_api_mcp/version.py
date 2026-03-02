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
_DEFAULT_VERSION = "5.7"
_UPDATE_CHECK_TIMEOUT = 3  # seconds
_DOWNLOAD_TIMEOUT = 60  # seconds per socket operation


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
        normalized = _normalize_version(env_ver)
        if normalized:
            return normalized
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
            normalized = _normalize_version(raw)
            if normalized:
                return normalized

    # 3. Default.
    return _DEFAULT_VERSION


def _normalize_version(raw: str) -> str | None:
    """Validate and normalize a version string.

    Accepts ``"5.7"``, ``"5.7.3"``, ``"5.7.3.1"`` etc.
    Returns ``"5.7"`` or ``"5.7.3"`` (at most major.minor.patch).
    """
    raw = raw.strip()
    m = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?(?:\.\d+)*", raw)
    if not m:
        return None
    if m.group(3):
        return f"{m.group(1)}.{m.group(2)}.{m.group(3)}"
    return f"{m.group(1)}.{m.group(2)}"


def _db_candidates(version: str) -> list[str]:
    """Return version strings to try, most specific first.

    ``"5.7.3"`` -> ``["5.7.3", "5.7"]``
    ``"5.7"``   -> ``["5.7"]``
    """
    candidates = [version]
    m = re.match(r"^(\d+\.\d+)\.\d+$", version)
    if m:
        candidates.append(m.group(1))
    return candidates


def _download(url: str, dest: Path, timeout: int = _DOWNLOAD_TIMEOUT) -> None:
    """Download a URL to a local file with a per-operation timeout."""
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                f.write(chunk)


def db_path(version: str | None = None) -> Path:
    """Return the path to the best available database for *version*.

    Checks candidates in order (e.g. ``5.7.3`` then ``5.7``) and returns
    the first that exists locally.  If none exist, returns the path for
    the most specific version (for subsequent download).
    """
    if version is None:
        version = detect_version()
    for v in _db_candidates(version):
        p = _CACHE_DIR / f"unreal_docs_{v}.db"
        if p.is_file() and p.stat().st_size > 0:
            return p
    return _CACHE_DIR / f"unreal_docs_{version}.db"


def _check_for_update(local_path: Path, version: str) -> None:
    """Check if a newer database is available and download it.

    Compares local file size with the remote Content-Length via a HEAD
    request.  Silently does nothing if the check fails (offline, timeout,
    no Content-Length header, etc.).
    """
    url = f"{_GITHUB_RELEASE}/unreal_docs_{version}.db"
    try:
        req = urllib.request.Request(url, method="HEAD")
        with urllib.request.urlopen(req, timeout=_UPDATE_CHECK_TIMEOUT) as resp:
            remote_size = int(resp.headers.get("Content-Length", 0))
        if remote_size <= 0:
            return
        local_size = local_path.stat().st_size
        if remote_size == local_size:
            return
        print(
            f"Database update available for UE {version} "
            f"(local: {local_size / 1024 / 1024:.1f} MB, "
            f"remote: {remote_size / 1024 / 1024:.1f} MB), downloading...",
            file=sys.stderr,
        )
        tmp = local_path.with_suffix(".db.tmp")
        try:
            _download(url, tmp)
            tmp.replace(local_path)
            size_mb = local_path.stat().st_size / 1024 / 1024
            print(
                f"  Updated -> {local_path} ({size_mb:.1f} MB)", file=sys.stderr
            )
        except Exception:
            tmp.unlink(missing_ok=True)
    except Exception:
        pass


def ensure_db(version: str | None = None) -> Path:
    """Ensure the database exists, downloading on first run if needed.

    For patch versions (e.g. ``5.7.3``), tries the patch-specific database
    first, then falls back to the major.minor database (``5.7``).
    Also checks for remote updates to the cached database.
    """
    if version is None:
        version = detect_version()

    candidates = _db_candidates(version)

    # Check local cache first.
    for v in candidates:
        path = _CACHE_DIR / f"unreal_docs_{v}.db"
        if path.is_file() and path.stat().st_size > 0:
            _check_for_update(path, v)
            return path

    # Try downloading each candidate.
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    for v in candidates:
        path = _CACHE_DIR / f"unreal_docs_{v}.db"
        url = f"{_GITHUB_RELEASE}/unreal_docs_{v}.db"
        print(f"Downloading Unreal {v} API database...", file=sys.stderr)
        print(f"  {url}", file=sys.stderr)

        tmp = path.with_suffix(".db.tmp")
        try:
            _download(url, tmp)
            tmp.replace(path)
            size_mb = path.stat().st_size / 1024 / 1024
            print(f"  Downloaded {size_mb:.1f} MB -> {path}", file=sys.stderr)
            return path
        except Exception:
            tmp.unlink(missing_ok=True)
            if v != candidates[-1]:
                print(f"  Not found, trying fallback...", file=sys.stderr)

    # All candidates failed.
    raise RuntimeError(
        f"Failed to download database for UE {version}.\n"
        f"Tried: {', '.join(f'unreal_docs_{v}.db' for v in candidates)}\n"
        f"URL base: {_GITHUB_RELEASE}\n\n"
        f"If you're building databases locally, run:\n"
        f"  python -m unreal_api_mcp.ingest --unreal-version {version}"
    )
