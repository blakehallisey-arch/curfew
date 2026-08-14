"""Loading the policy.

The shipped default profile is the floor. A repo's `.curfew.json` is merged on
top of it, and the merge is deliberately additive for the lists that protect
things: your `protect` entries are ADDED to the defaults, they do not replace
them. If you genuinely want to drop a default rule you say so out loud with
`protect_remove`. Silent narrowing of a deny list is how a guard rots.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

PROFILE = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "profiles", "default.json")

CONFIG_NAMES = (".curfew.json", ".curfew/config.json")

# Lists where the repo config ADDS to the default rather than replacing it.
_ADDITIVE = ("protect", "shell_deny", "read_only_programs", "chain_characters")


def load_profile() -> Dict[str, Any]:
    with open(PROFILE) as f:
        return json.load(f)


def find_config(start: Optional[str] = None) -> Optional[str]:
    """Walk up from `start` looking for a config, stopping at the filesystem
    root or at a `.git` directory — whichever comes first. Stopping at `.git`
    matters: a run inside a submodule should not silently pick up the parent
    repo's policy."""
    cur = os.path.abspath(start or os.getcwd())
    while True:
        for name in CONFIG_NAMES:
            path = os.path.join(cur, name)
            if os.path.isfile(path):
                return path
        if os.path.isdir(os.path.join(cur, ".git")):
            return None
        parent = os.path.dirname(cur)
        if parent == cur:
            return None
        cur = parent


def merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(base)
    for key, val in over.items():
        if key.endswith("_remove"):
            continue
        if key in _ADDITIVE and isinstance(val, list):
            seen = list(base.get(key) or [])
            for item in val:
                if item not in seen:
                    seen.append(item)
            out[key] = seen
        elif isinstance(val, dict) and isinstance(base.get(key), dict):
            out[key] = merge(base[key], val)
        else:
            out[key] = val

    for key in _ADDITIVE:
        drop = over.get(key + "_remove")
        if drop:
            out[key] = [r for r in out.get(key, []) if r not in drop]
    return out


def load(start: Optional[str] = None) -> Dict[str, Any]:
    cfg = load_profile()
    path = find_config(start)
    if path:
        try:
            with open(path) as f:
                cfg = merge(cfg, json.load(f))
        except Exception as exc:
            # A config we cannot parse is not a reason to run without a policy.
            # Keep the defaults and record that we did.
            cfg["_config_error"] = "{}: {}".format(path, exc)
        cfg["_config_path"] = path
    return cfg


def repo_root(start: Optional[str] = None) -> str:
    path = find_config(start)
    if path:
        d = os.path.dirname(path)
        return os.path.dirname(d) if os.path.basename(d) == ".curfew" else d
    cur = os.path.abspath(start or os.getcwd())
    while True:
        if os.path.isdir(os.path.join(cur, ".git")):
            return cur
        parent = os.path.dirname(cur)
        if parent == cur:
            return os.path.abspath(start or os.getcwd())
        cur = parent


def armed(cfg: Dict[str, Any], env: Optional[Dict[str, str]] = None) -> bool:
    """Is this an unattended run?

    Curfew has NO opinion about a session a human is driving. The whole design
    rests on that: a policy strict enough to be worth having would be
    infuriating to work under by hand, and a policy loose enough to work under
    by hand is not worth having at 3am.

    So the runner has to say. It sets one of the `arm_when_env` variables, and
    nothing else does. `always_on` is there for someone who wants it everywhere
    and knows what they are asking for.
    """
    if not cfg.get("enabled", True):
        return False
    if cfg.get("always_on"):
        return True
    e = os.environ if env is None else env
    return any(e.get(name) == "1" for name in cfg.get("arm_when_env") or [])
