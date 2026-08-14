"""The PreToolUse hook.

Reads a tool call as JSON on stdin, writes a decision as JSON on stdout.

THE ONE THING TO GET RIGHT: printing nothing is read as ALLOW. So an exception
anywhere in here does not mean "no decision", it means "yes, go ahead" — and
it means that at the exact moment something unexpected is happening, which is
the worst possible time to be permissive.

That is why `main()` is wrapped and why the fallback is a deliberate deny for
anything that can change the world, not a shrug. `on_error: "allow"` is
available for people who would rather have a run that keeps going, and it is
not the default.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Dict

# The hook is wired into Claude Code by absolute path — `python3 .../curfew/
# hook.py` — so it runs as a loose script with no parent package and the
# relative import below raises ImportError before anything else happens. That
# traceback goes to stderr, stdout stays empty, and an empty stdout is read as
# ALLOW. The guard would have been installed, visible in settings.json, and
# silently permitting everything. Found by running the documented install
# command; worth the four lines.
if __package__ in (None, ""):
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from curfew import audit, config, policy
else:
    from . import audit, config, policy


def _emit(decision: Dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(decision))
    sys.stdout.flush()
    sys.exit(0)


def _allow() -> None:
    _emit({})


def _deny(reason: str) -> None:
    _emit({"hookSpecificOutput": {
        "hookEventName": "PreToolUse",
        "permissionDecision": "deny",
        "permissionDecisionReason": reason,
    }})


def run(payload: Dict[str, Any], env: Dict[str, str]) -> Dict[str, Any]:
    """The testable core. Returns the hook output dict."""
    cwd = payload.get("cwd") or os.getcwd()
    cfg = config.load(cwd)
    root = config.repo_root(cwd)

    if not config.armed(cfg, env):
        return {}

    tool = payload.get("tool_name") or ""
    args = payload.get("tool_input") or {}
    decision = policy.decide(tool, args, cfg, env)

    audit.write(cfg, root, {
        "tool": tool,
        "action": decision.action,
        "category": decision.category,
        "rule": decision.rule,
        "target": str(args.get("file_path") or args.get("notebook_path")
                      or args.get("command") or "")[:400],
        "session": payload.get("session_id"),
        "phase": env.get("CURFEW_PHASE") or None,
        "day": env.get((cfg.get("day_mode") or {}).get("env") or "") == "1",
    })

    if decision.denied:
        return {"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": decision.reason,
        }}
    return {}


# Anything that can change something outside this process. If curfew itself
# breaks, these are the calls that do not get the benefit of the doubt.
_CONSEQUENTIAL = set(policy.WRITE_TOOLS) | {"Bash"}


def main() -> None:
    raw = ""
    try:
        raw = sys.stdin.read()
    except Exception:
        pass

    try:
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        # A payload we cannot read is not a payload we can judge. We also
        # cannot tell what it was going to do. Allow, and say so in the log if
        # we can — a malformed frame is a harness bug, not an attack, and
        # denying every call because the harness hiccuped breaks the run.
        _allow()
        return

    try:
        _emit(run(payload, dict(os.environ)))
    except SystemExit:
        raise
    except Exception as exc:
        tool = payload.get("tool_name") or ""
        try:
            cfg = config.load(payload.get("cwd") or os.getcwd())
            mode = cfg.get("on_error") or "deny"
        except Exception:
            mode = "deny"
        consequential = tool in _CONSEQUENTIAL or tool.startswith("mcp__")
        if mode == "deny" and consequential:
            _deny(
                "curfew failed while judging this call and refused it rather "
                "than waving it through, because a guard that cannot decide "
                "must not be the reason something got out. The error was: "
                "{}: {}. Fix the policy file or set on_error to 'allow'. "
                "Record this item as blocked.".format(type(exc).__name__, exc))
        _allow()


if __name__ == "__main__":
    main()
