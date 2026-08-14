"""The log.

One JSON object per line, appended. It exists because of a specific failure:
the run's own account of what it did goes stale the moment a write gets
blocked. It finishes the work, gets refused when it tries to record that it
finished, and every board downstream shows the item stuck forever.

So the log is written by the guard, not by the thing being guarded. The guard
is the one process in the loop that cannot be talked out of it.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())


def path_for(cfg: Dict[str, Any], root: str) -> str:
    rel = ((cfg.get("audit") or {}).get("path")) or ".curfew/audit.jsonl"
    return rel if os.path.isabs(rel) else os.path.join(root, rel)


def write(cfg: Dict[str, Any], root: str, record: Dict[str, Any]) -> None:
    """Never raises. A logger that can break the run it is watching is worse
    than no logger — you would find out by the run dying, at 3am."""
    try:
        audit = cfg.get("audit") or {}
        if record.get("action") == "allow" and not audit.get("log_allows", True):
            return
        p = path_for(cfg, root)
        d = os.path.dirname(p)
        if d:
            os.makedirs(d, exist_ok=True)
        cap = int(audit.get("max_bytes") or 0)
        if cap and os.path.exists(p) and os.path.getsize(p) > cap:
            try:
                os.replace(p, p + ".1")
            except Exception:
                pass
        record = dict(record)
        record.setdefault("at", _now())
        with open(p, "a") as f:
            f.write(json.dumps(record, sort_keys=True) + "\n")
    except Exception:
        pass


def read(cfg: Dict[str, Any], root: str, since: Optional[str] = None
         ) -> List[Dict[str, Any]]:
    p = path_for(cfg, root)
    rows: List[Dict[str, Any]] = []
    for candidate in (p + ".1", p):
        if not os.path.exists(candidate):
            continue
        try:
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    if since and str(row.get("at", "")) < since:
                        continue
                    rows.append(row)
        except Exception:
            continue
    return rows


def summarize(rows: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    rows = list(rows)
    allows = [r for r in rows if r.get("action") == "allow"]
    denies = [r for r in rows if r.get("action") == "deny"]
    by_cat: Dict[str, int] = {}
    by_rule: Dict[str, int] = {}
    for r in denies:
        by_cat[r.get("category") or "-"] = by_cat.get(r.get("category") or "-", 0) + 1
        rule = r.get("rule") or "-"
        by_rule[rule] = by_rule.get(rule, 0) + 1
    tools: Dict[str, int] = {}
    for r in rows:
        t = r.get("tool") or "-"
        tools[t] = tools.get(t, 0) + 1
    return {
        "total": len(rows),
        "allowed": len(allows),
        "denied": len(denies),
        "first": rows[0].get("at") if rows else None,
        "last": rows[-1].get("at") if rows else None,
        "by_category": dict(sorted(by_cat.items(), key=lambda kv: -kv[1])),
        "by_rule": dict(sorted(by_rule.items(), key=lambda kv: -kv[1])),
        "by_tool": dict(sorted(tools.items(), key=lambda kv: -kv[1])),
        "denies": denies,
    }
