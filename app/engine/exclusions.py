"""User exclusion rules: paths or glob patterns that are never flagged."""
from __future__ import annotations
import fnmatch
from typing import Iterable, List, Tuple

_GLOB_CHARS = set("*?[")


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


class ExclusionMatcher:
    """Matches a path against folder/file paths (prefix) and glob patterns.

    A rule containing *, ? or [ is a pattern, matched against the item's name
    and its full path. Any other rule is a path: it excludes that file or
    folder and everything inside it.
    """

    def __init__(self, rules: Iterable[str]):
        self.paths: List[str] = []
        self.patterns: List[str] = []
        for rule in rules:
            rule = (rule or "").strip()
            if not rule:
                continue
            if _GLOB_CHARS & set(rule):
                self.patterns.append(_norm(rule))
            else:
                self.paths.append(_norm(rule))

    def __bool__(self) -> bool:
        return bool(self.paths or self.patterns)

    def matches(self, path: str, name: str | None = None) -> bool:
        if not self:
            return False
        norm = _norm(path)
        for p in self.paths:
            if norm == p or norm.startswith(p + "/"):
                return True
        if self.patterns:
            lname = (name if name is not None else norm.rsplit("/", 1)[-1]).lower()
            for pat in self.patterns:
                if fnmatch.fnmatchcase(lname, pat) or fnmatch.fnmatchcase(norm, pat):
                    return True
        return False


def split_rules(rules: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Return (paths, patterns) for display."""
    m = ExclusionMatcher(rules)
    return m.paths, m.patterns
