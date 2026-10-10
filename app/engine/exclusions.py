"""Path and pattern rules: exclusions (never flagged) and user junk rules (always flagged for review)."""
from __future__ import annotations
import fnmatch
import re
from typing import Iterable, List, Optional, Tuple

_GLOB_CHARS = set("*?[")


def _norm(path: str) -> str:
    return path.replace("\\", "/").rstrip("/").lower()


class ExclusionMatcher:
    """Matches a path against folder/file paths (prefix) and glob patterns.

    A rule containing *, ? or [ is a pattern, matched against the item's name
    and its full path. Any other rule is a path: it matches that file or
    folder and everything inside it.
    """

    def __init__(self, rules: Iterable[str]):
        # (normalized rule, rule as the user wrote it)
        self._paths: List[Tuple[str, str]] = []
        self._patterns: List[Tuple[str, str]] = []
        for rule in rules:
            rule = (rule or "").strip()
            if not rule:
                continue
            if _GLOB_CHARS & set(rule):
                self._patterns.append((_norm(rule), rule))
            else:
                self._paths.append((_norm(rule), rule))

    @property
    def paths(self) -> List[str]:
        return [n for n, _ in self._paths]

    @property
    def patterns(self) -> List[str]:
        return [n for n, _ in self._patterns]

    def __bool__(self) -> bool:
        return bool(self._paths or self._patterns)

    def match(self, path: str, name: str | None = None) -> Optional[str]:
        """The first rule (as written) that matches the path, or None."""
        if not self:
            return None
        norm = _norm(path)
        for p, rule in self._paths:
            if norm == p or norm.startswith(p + "/"):
                return rule
        if self._patterns:
            lname = (name if name is not None else norm.rsplit("/", 1)[-1]).lower()
            for pat, rule in self._patterns:
                if fnmatch.fnmatchcase(lname, pat) or fnmatch.fnmatchcase(norm, pat):
                    return rule
        return None

    def matches(self, path: str, name: str | None = None) -> bool:
        return self.match(path, name) is not None


def split_rules(rules: Iterable[str]) -> Tuple[List[str], List[str]]:
    """Return (paths, patterns) for display."""
    m = ExclusionMatcher(rules)
    return m.paths, m.patterns


def rule_too_broad(rule: str) -> bool:
    """True for a junk rule that would flag everything, such as "*", "*.*" or a whole drive."""
    rule = (rule or "").strip()
    if not rule:
        return True
    if _GLOB_CHARS & set(rule):
        # Nothing left but wildcards, dots and separators: "*", "*.*", "**/*", "?*"
        return not re.sub(r"[*?.\\/\[\]!]", "", rule)
    norm = _norm(rule)
    return norm in ("", "/") or bool(re.fullmatch(r"[a-z]:", norm))
