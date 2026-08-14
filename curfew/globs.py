"""Path matching.

Two kinds of rule, because both are genuinely useful and telling them apart is
cheap:

  * A GLOB — anything containing `*`, `?` or `[`. Matched against the path.
    `**` crosses directory separators, `*` does not. This is what people expect
    and it is NOT what `fnmatch` does (fnmatch turns `*` into `.*`, so
    `src/*.py` happily matches `src/a/b/c.py`). So we compile our own.

  * A FRAGMENT — anything else. Matched as a plain substring, case-insensitive.
    `.env` protects `.env`, `.env.local`, `config/.env.production` and
    `backup-of-.env` without anyone having to think about it.

Fragments are blunt on purpose. A protection list that only works when you
guessed the exact shape of the filename is a protection list that fails the one
night it matters.
"""
from __future__ import annotations

import re
from typing import Iterable, List, Optional, Tuple

_GLOB_CHARS = ("*", "?", "[")


def is_glob(rule: str) -> bool:
    return any(c in rule for c in _GLOB_CHARS)


def _compile_glob(pat: str) -> "re.Pattern[str]":
    """Translate a glob to a regex. `**` crosses `/`, `*` and `?` do not."""
    out: List[str] = ["(?s)"]
    i = 0
    n = len(pat)
    while i < n:
        c = pat[i]
        if c == "*":
            # `**/` -> zero or more leading directories. `**` -> anything.
            if pat[i:i + 3] == "**/":
                out.append(r"(?:.*/)?")
                i += 3
                continue
            if pat[i:i + 2] == "**":
                out.append(r".*")
                i += 2
                continue
            out.append(r"[^/]*")
            i += 1
        elif c == "?":
            out.append(r"[^/]")
            i += 1
        elif c == "[":
            j = i + 1
            if j < n and pat[j] in ("!", "^"):
                j += 1
            if j < n and pat[j] == "]":
                j += 1
            while j < n and pat[j] != "]":
                j += 1
            if j >= n:
                out.append(re.escape("["))
                i += 1
                continue
            body = pat[i + 1:j]
            if body.startswith("!"):
                body = "^" + body[1:]
            out.append("[" + body.replace("\\", "\\\\") + "]")
            i = j + 1
        else:
            out.append(re.escape(c))
            i += 1
    return re.compile("".join(out) + r"\Z", re.IGNORECASE)


_CACHE = {}


def _cached(pat: str) -> "re.Pattern[str]":
    got = _CACHE.get(pat)
    if got is None:
        got = _compile_glob(pat)
        _CACHE[pat] = got
    return got


def normalize(path: str) -> str:
    """Windows separators out, leading `./` out. Nothing else — we never
    resolve symlinks or make the path absolute, because a rule should apply to
    what was written, not to where it happened to land."""
    p = (path or "").replace("\\", "/")
    while p.startswith("./"):
        p = p[2:]
    return p


def matches(path: str, rule: str) -> bool:
    p = normalize(path)
    if is_glob(rule):
        r = normalize(rule)
        if _cached(r).match(p):
            return True
        # A bare-name glob like `*.pem` should also match `deep/in/here.pem`.
        # Anchoring it at the root only is the kind of subtlety that turns a
        # protection rule into a decoration.
        if "/" not in r:
            return bool(_cached("**/" + r).match(p))
        return False
    return rule.lower() in p.lower()


def first_match(path: str, rules: Iterable[str]) -> Optional[str]:
    """The rule that matched, so the denial can say which one and the human can
    go delete it if it was wrong."""
    for rule in rules:
        if rule and matches(path, rule):
            return rule
    return None


def any_match(paths: Iterable[str], rules: Iterable[str]) -> Optional[Tuple[str, str]]:
    rules = list(rules)
    for p in paths:
        hit = first_match(p, rules)
        if hit:
            return (p, hit)
    return None
