"""Locate Unreal Engine installs and discover header directories."""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Iterator

# ---------------------------------------------------------------------------
# Default install locations (Windows-centric for now)
# ---------------------------------------------------------------------------

_EPIC_LAUNCHER_MANIFESTS = [
    Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData"))
    / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat",
]


def find_unreal_install(version: str) -> Path:
    """Locate the Unreal Engine install directory for *version*.

    Priority:
      1. ``UNREAL_INSTALL_PATH`` env var
      2. Epic Games Launcher manifest (``LauncherInstalled.dat``)
      3. Common default paths

    Raises ``FileNotFoundError`` if nothing is found.
    """
    # 1. Env var override.
    env = os.environ.get("UNREAL_INSTALL_PATH")
    if env:
        p = Path(env)
        if _is_valid_install(p):
            return p
        raise FileNotFoundError(
            f"UNREAL_INSTALL_PATH={env!r} does not look like a valid UE install "
            f"(expected Engine/Source/ subdirectory)."
        )

    # 2. Epic Games Launcher manifest.
    for manifest_path in _EPIC_LAUNCHER_MANIFESTS:
        result = _search_launcher_manifest(manifest_path, version)
        if result:
            return result

    # 3. Common default paths.
    for candidate in _default_install_candidates(version):
        if _is_valid_install(candidate):
            return candidate

    raise FileNotFoundError(
        f"Could not find Unreal Engine {version} install.\n"
        f"Set UNREAL_INSTALL_PATH to point to your UE_{version} directory."
    )


def _is_valid_install(path: Path) -> bool:
    """Check if a path looks like a UE install root."""
    return (path / "Engine" / "Source").is_dir()


def _search_launcher_manifest(manifest: Path, version: str) -> Path | None:
    """Search the Epic Games Launcher manifest for a matching install."""
    if not manifest.is_file():
        return None
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
        for entry in data.get("InstallationList", []):
            install_path = Path(entry.get("InstallLocation", ""))
            app_name = entry.get("AppName", "")
            # App names look like "UE_5.5", "UE_5.6", etc.
            if version in app_name and _is_valid_install(install_path):
                return install_path
    except (json.JSONDecodeError, KeyError, OSError):
        pass
    return None


def _default_install_candidates(version: str) -> list[Path]:
    """Generate common default paths for a UE version."""
    candidates = []
    # Check all drive letters on Windows.
    if sys.platform == "win32":
        for drive in "CDEFGHIJ":
            candidates.append(Path(f"{drive}:/UE_{version}"))
            candidates.append(Path(f"{drive}:/Epic Games/UE_{version}"))
            candidates.append(Path(f"{drive}:/Program Files/Epic Games/UE_{version}"))
    else:
        home = Path.home()
        candidates.append(home / "UnrealEngine" / f"UE_{version}")
        candidates.append(Path(f"/opt/UnrealEngine/UE_{version}"))
    return candidates


# ---------------------------------------------------------------------------
# Module discovery
# ---------------------------------------------------------------------------


def discover_header_dirs(install_path: Path) -> list[tuple[str, Path]]:
    """Find all module header directories under an Unreal install.

    Returns list of (module_name, header_root_dir) tuples.  Each
    header_root_dir is a ``Classes/`` or ``Public/`` directory containing
    the public API headers for that module.

    Scans:
      - ``Engine/Source/Runtime/*/``
      - ``Engine/Source/Editor/*/``
      - ``Engine/Source/Developer/*/``
      - ``Engine/Plugins/**/Source/*/``
    """
    source = install_path / "Engine" / "Source"
    plugins = install_path / "Engine" / "Plugins"
    results: list[tuple[str, Path]] = []

    # Source modules (Runtime, Editor, Developer).
    for category in ("Runtime", "Editor", "Developer"):
        cat_dir = source / category
        if not cat_dir.is_dir():
            continue
        for module_dir in sorted(cat_dir.iterdir()):
            if not module_dir.is_dir():
                continue
            module_name = module_dir.name
            for subdir_name in ("Classes", "Public"):
                subdir = module_dir / subdir_name
                if subdir.is_dir():
                    results.append((module_name, subdir))

    # Plugin modules.
    if plugins.is_dir():
        for plugin_source in _find_plugin_source_dirs(plugins):
            module_name = plugin_source.name
            for subdir_name in ("Classes", "Public"):
                subdir = plugin_source / subdir_name
                if subdir.is_dir():
                    results.append((module_name, subdir))

    return results


def _find_plugin_source_dirs(plugins_root: Path) -> Iterator[Path]:
    """Recursively find plugin source module directories.

    Plugin layout:  ``Plugins/{name}/Source/{ModuleName}/``
    Some plugins nest deeper: ``Plugins/{category}/{name}/Source/{ModuleName}/``
    """
    for source_dir in plugins_root.rglob("Source"):
        if not source_dir.is_dir():
            continue
        for child in source_dir.iterdir():
            if child.is_dir() and (child / "Public").is_dir() or (child / "Classes").is_dir():
                yield child
        # Also check if Source/ itself has Public/Classes (flat layout).
        if (source_dir / "Public").is_dir() or (source_dir / "Classes").is_dir():
            yield source_dir


def collect_headers(
    header_dirs: list[tuple[str, Path]],
) -> list[tuple[str, str, Path]]:
    """Walk header directories and collect all .h files.

    Returns list of (module_name, include_path, file_path) tuples.
    The include_path is the path relative to the module's header root
    (what you'd write in ``#include "..."``).
    """
    results: list[tuple[str, str, Path]] = []
    for module_name, root in header_dirs:
        for h_file in root.rglob("*.h"):
            # Skip generated headers.
            if h_file.name.endswith(".generated.h"):
                continue
            include_path = h_file.relative_to(root).as_posix()
            results.append((module_name, include_path, h_file))
    return results


# ---------------------------------------------------------------------------
# Version from .uproject
# ---------------------------------------------------------------------------


def read_uproject_version(project_path: str | Path) -> str | None:
    """Read the engine version from a .uproject file's EngineAssociation field."""
    path = Path(project_path)
    if path.is_dir():
        uprojects = list(path.glob("*.uproject"))
        if not uprojects:
            return None
        path = uprojects[0]
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        assoc = data.get("EngineAssociation", "")
        # EngineAssociation is like "5.5" or a GUID for source builds.
        m = re.match(r"^(\d+\.\d+)", assoc)
        return m.group(1) if m else None
    except (json.JSONDecodeError, OSError):
        return None
