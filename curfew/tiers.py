"""Auto-merge, or stop at a pull request.

The whole idea in one sentence: **the thing that proposes the work is not the
thing that authorizes it.**

An unattended run that decides its own change is routine will, eventually,
decide that about a change that is not. Not because it is devious — because
"is this routine" is a judgment call and it is making it about its own work,
alone, at three in the morning, with no one to disagree.

So the tier is re-derived here, from the policy file, using only facts that are
true of the diff: which files it touches, how big it is, and what kind of work
it was queued as. If the run claimed tier 1 and this says tier 2, that
disagreement is itself worth printing.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import globs


class Ruling(object):
    __slots__ = ("tier", "why", "rule")

    def __init__(self, tier: int, why: str, rule: Optional[str] = None):
        self.tier = tier
        self.why = why
        self.rule = rule

    @property
    def may_auto_merge(self) -> bool:
        return self.tier == 1

    def as_dict(self) -> Dict[str, Any]:
        return {"tier": self.tier, "may_auto_merge": self.may_auto_merge,
                "why": self.why, "rule": self.rule}


def tier_for(paths: Iterable[str], cfg: Dict[str, Any],
             kind: Optional[str] = None,
             lines_changed: Optional[int] = None) -> Ruling:
    paths = [p for p in paths if p]
    t = cfg.get("tiers") or {}
    never = t.get("never_tier1") or []
    kinds = t.get("tier1_kinds") or []
    max_lines = t.get("tier1_max_lines")

    if not paths:
        return Ruling(2, "no files named, so nothing here can be checked "
                         "against the policy")

    hit = globs.any_match(paths, never)
    if hit:
        return Ruling(2, "{} matches never_tier1 rule '{}'".format(hit[0], hit[1]),
                      hit[1])

    if max_lines is not None and lines_changed is not None and lines_changed > max_lines:
        return Ruling(2, "{} lines changed, over the tier1_max_lines of {}".format(
            lines_changed, max_lines))

    if kinds:
        if not kind:
            return Ruling(2, "no kind was declared, and tier 1 is limited to: "
                             "{}".format(", ".join(kinds)))
        if kind not in kinds:
            return Ruling(2, "kind '{}' is not on the tier-1 list ({})".format(
                kind, ", ".join(kinds)))

    return Ruling(1, "no protected path, and kind '{}' is routine".format(
        kind or "unspecified"))


def check_claim(claimed: Optional[int], ruling: Ruling) -> Optional[str]:
    """A run that claims tier 1 on work this says is tier 2 is the interesting
    case. It is usually honest and wrong. It is occasionally the only warning
    you get."""
    if claimed is None:
        return None
    if int(claimed) == ruling.tier:
        return None
    return ("the run claimed tier {}, the policy says tier {} — {}".format(
        claimed, ruling.tier, ruling.why))


def changed_paths(base: str = "HEAD") -> Tuple[List[str], int]:
    """Files changed against `base`, plus the line count. Uncommitted work
    included, because that is what an unattended run has in front of it."""
    import subprocess
    paths: List[str] = []
    lines = 0
    try:
        out = subprocess.run(["git", "diff", "--numstat", base],
                             capture_output=True, text=True, timeout=20)
        blob = out.stdout or ""
        out2 = subprocess.run(["git", "diff", "--numstat", "--cached", base],
                              capture_output=True, text=True, timeout=20)
        blob += out2.stdout or ""
        for row in blob.splitlines():
            parts = row.split("\t")
            if len(parts) != 3:
                continue
            add, rem, path = parts
            paths.append(path)
            for n in (add, rem):
                if n.isdigit():
                    lines += int(n)
    except Exception:
        return ([], 0)
    return (sorted(set(paths)), lines)
