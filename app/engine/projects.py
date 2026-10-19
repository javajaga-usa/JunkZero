"""Dependency and build folders of coding projects nobody has touched for months.

node_modules, Python virtual environments (.venv), Rust and Maven target folders, Gradle and
CMake build folders and .NET bin/obj folders can be built again from the project's own files,
and they are often the biggest thing in an old project. A folder is listed only when:
  * the project file that makes it rebuildable sits right next to it (package.json for
    node_modules, Cargo.toml or pom.xml for target, a .csproj for bin and obj...), and
  * nothing in the project itself (its source files, its Git index) changed for
    OLD_PROJECT_DAYS days. A project too big to check fully is not listed.
"""
from __future__ import annotations
import os
import time
from typing import Optional, Tuple

from app.config import BUILD_DIR_NAMES, OLD_PROJECT_DAYS, VCS_DIR_NAMES
from app.engine.classifier import folder_names

# Entries looked at per project when finding its newest change
ACTIVITY_WALK_LIMIT = 20000
VENV_DIR_NAMES = {".venv", "venv", "env", ".env"}
_PYTHON_PROJECT_FILES = {"pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "pipfile", "poetry.lock"}
_DOTNET_SUFFIXES = (".csproj", ".vbproj", ".fsproj", ".sln")
# Folders skipped when looking for the project's newest change: what they hold is rebuilt or
# downloaded, so it says nothing about whether someone works on the project
_NOT_WORK = BUILD_DIR_NAMES | VENV_DIR_NAMES | VCS_DIR_NAMES | {"dist", ".idea", ".vs", ".vscode", ".tox", ".nox"}


def rebuild_hint(path: str, name: str) -> Optional[str]:
    """How to get the folder back, if it is a dependency or build folder of the project around
    it; None otherwise."""
    lower = name.lower()
    project = os.path.dirname(path)
    names = folder_names(project)
    if lower == "node_modules" and "package.json" in names:
        return "npm install brings it back"
    if lower == ".next" and "package.json" in names:
        return "npm run build brings it back"
    if lower in VENV_DIR_NAMES and os.path.isfile(os.path.join(path, "pyvenv.cfg")) and names & _PYTHON_PROJECT_FILES:
        return "a new virtual environment and pip install bring it back"
    if lower == "target":
        if "cargo.toml" in names:
            return "cargo build brings it back"
        if "pom.xml" in names:
            return "mvn package brings it back"
        if "build.sbt" in names:
            return "sbt compile brings it back"
    if lower in ("build", ".gradle") and names & {"build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts"}:
        return "a Gradle build brings it back"
    if lower == "build" and "cmakelists.txt" in names:
        return "a CMake build brings it back"
    if lower in ("bin", "obj") and any(n.endswith(_DOTNET_SUFFIXES) for n in names):
        return "building the project in Visual Studio (or dotnet build) brings it back"
    return None


def last_activity(project: str, limit: int = ACTIVITY_WALK_LIMIT) -> float:
    """Newest change in the project's own files and folders (and its Git index), skipping
    dependency, build and version-control folders. 0 when it can't be told (too big, unreadable)."""
    try:
        newest = os.stat(project, follow_symlinks=False).st_mtime
    except OSError:
        return 0.0
    for git_file in (".git/index", ".git/HEAD"):
        try:
            newest = max(newest, os.stat(os.path.join(project, *git_file.split("/"))).st_mtime)
        except OSError:
            pass
    seen = 0
    for root, dirs, files in os.walk(project):
        dirs[:] = [d for d in dirs if d.lower() not in _NOT_WORK and not os.path.islink(os.path.join(root, d))]
        for name in dirs + files:
            seen += 1
            if seen > limit:
                return 0.0
            try:
                newest = max(newest, os.stat(os.path.join(root, name), follow_symlinks=False).st_mtime)
            except OSError:
                continue
    return newest


def old_project_folder(path: str, name: str, now: Optional[float] = None, days: int = OLD_PROJECT_DAYS,
                       activity=last_activity) -> Optional[Tuple[str, int, float]]:
    """(reason, days untouched, last change) when path is a rebuildable folder of a project
    untouched for `days` days; None otherwise."""
    hint = rebuild_hint(path, name)
    if hint is None:
        return None
    project = os.path.dirname(path)
    changed = activity(project)
    try:
        # Installing or building recently also counts as using the project
        changed = max(changed, os.stat(path, follow_symlinks=False).st_mtime) if changed > 0 else 0.0
    except OSError:
        return None
    now = time.time() if now is None else now
    untouched = int((now - changed) / 86400) if changed > 0 else 0
    if changed <= 0 or untouched < days:
        return None
    reason = (f"Project '{os.path.basename(project)}' untouched for {untouched} days; "
              f"{name} can go, {hint}")
    return reason, untouched, changed
