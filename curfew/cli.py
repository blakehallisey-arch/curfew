"""The command line.

Five verbs:

  curfew init      write a starter .curfew.json
  curfew check     ask the policy about one call, without running anything
  curfew tier      would this change auto-merge, or stop at a pull request
  curfew report    what happened last night, in plain english
  curfew explain   which rules are live right now and where they came from

`check` is the one that earns the tool trust. You can interrogate the policy
before you ever point it at a real run.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List

from . import audit, config, globs, policy, tiers

STARTER = """{
  "//": "curfew policy. Merged on top of the shipped defaults; list entries here are ADDED to them, not replacing them. See `curfew explain` for what is live.",

  "arm_when_env": ["UNATTENDED_RUN"],

  "protect": [],

  "shell_deny": [],

  "connectors": {
    "allow": []
  },

  "tiers": {
    "never_tier1": ["**/*.html", "**/*.css", "**/migrations/**"],
    "tier1_kinds": ["typo", "docs", "lint", "dead-code", "comment", "test-fix"],
    "tier1_max_lines": 120
  }
}
"""


def _p(msg: str = "") -> None:
    sys.stdout.write(msg + "\n")


def cmd_init(args) -> int:
    root = os.path.abspath(args.path or os.getcwd())
    target = os.path.join(root, ".curfew.json")
    if os.path.exists(target) and not args.force:
        _p("There is already a .curfew.json here. Use --force to overwrite it.")
        return 1
    with open(target, "w") as f:
        f.write(STARTER)
    os.makedirs(os.path.join(root, ".curfew"), exist_ok=True)
    gi = os.path.join(root, ".gitignore")
    line = ".curfew/\n"
    try:
        existing = open(gi).read() if os.path.exists(gi) else ""
        if ".curfew/" not in existing:
            with open(gi, "a") as f:
                if existing and not existing.endswith("\n"):
                    f.write("\n")
                f.write(line)
            _p("Added .curfew/ to .gitignore")
    except Exception:
        _p("Could not update .gitignore — add '.curfew/' to it yourself.")
    _p("Wrote {}".format(target))
    _p("")
    _p("Next: run install.sh to wire the hook into Claude Code, then have your")
    _p("runner export UNATTENDED_RUN=1 before it starts an unattended session.")
    _p("Nothing is enforced until that variable is set.")
    return 0


def cmd_check(args) -> int:
    cfg = config.load(os.getcwd())
    env = dict(os.environ)
    for name in cfg.get("arm_when_env") or ["UNATTENDED_RUN"]:
        env[name] = "1"
    if args.phase:
        env["CURFEW_PHASE"] = args.phase
    if args.day:
        env[(cfg.get("day_mode") or {}).get("env") or "CURFEW_DAY"] = "1"

    if args.write:
        tool, tool_args = "Write", {"file_path": args.write}
    elif args.bash:
        tool, tool_args = "Bash", {"command": args.bash}
    elif args.tool:
        tool, tool_args = args.tool, json.loads(args.args or "{}")
    else:
        _p("Give me something to check: --write PATH, --bash 'CMD', or "
           "--tool NAME --args JSON")
        return 1

    d = policy.decide(tool, tool_args, cfg, env)
    if args.json:
        _p(json.dumps(d.as_dict(), indent=2))
        return 0 if not d.denied else 3
    if d.denied:
        _p("DENY  [{}]  rule: {}".format(d.category, d.rule))
        _p("")
        _p(d.reason)
        return 3
    _p("ALLOW")
    return 0


def cmd_tier(args) -> int:
    cfg = config.load(os.getcwd())
    paths: List[str] = list(args.paths or [])
    lines = args.lines
    if not paths:
        paths, lines = tiers.changed_paths(args.base)
        if not paths:
            _p("No changed files against {} — nothing to rule on.".format(args.base))
            return 1
    ruling = tiers.tier_for(paths, cfg, kind=args.kind, lines_changed=lines)
    warn = tiers.check_claim(args.claimed, ruling)
    if args.json:
        out = ruling.as_dict()
        out["paths"] = paths
        out["lines_changed"] = lines
        out["disagreement"] = warn
        _p(json.dumps(out, indent=2))
        return 0 if ruling.may_auto_merge else 3
    _p("tier {} — {}".format(ruling.tier, "may auto-merge"
                             if ruling.may_auto_merge else "stops at a pull request"))
    _p("why: {}".format(ruling.why))
    _p("files: {}{}".format(", ".join(paths[:8]),
                            " (+{} more)".format(len(paths) - 8) if len(paths) > 8 else ""))
    if lines:
        _p("lines changed: {}".format(lines))
    if warn:
        _p("")
        _p("DISAGREEMENT: {}".format(warn))
    return 0 if ruling.may_auto_merge else 3


def cmd_report(args) -> int:
    cfg = config.load(os.getcwd())
    root = config.repo_root(os.getcwd())
    rows = audit.read(cfg, root, since=args.since)
    s = audit.summarize(rows)
    if args.json:
        _p(json.dumps(s, indent=2))
        return 0
    if not rows:
        _p("Nothing logged yet at {}.".format(audit.path_for(cfg, root)))
        _p("Either no unattended run has happened, or the runner never set the")
        _p("arming variable — check with `curfew explain`.")
        return 1
    _p("{} calls from {} to {}".format(s["total"], s["first"], s["last"]))
    _p("{} allowed, {} refused".format(s["allowed"], s["denied"]))
    if s["denied"]:
        _p("")
        _p("Refused, by reason:")
        for cat, n in s["by_category"].items():
            _p("  {:<10} {}".format(cat, n))
        _p("")
        _p("Refused, by rule:")
        for rule, n in list(s["by_rule"].items())[:12]:
            _p("  {:<28} {}".format(str(rule)[:28], n))
        _p("")
        _p("The last few refusals:")
        for r in s["denies"][-args.tail:]:
            _p("  {}  {}  {}".format(r.get("at"), r.get("tool"),
                                     str(r.get("target") or "")[:90]))
    else:
        _p("")
        _p("Nothing was refused. That is either a quiet night or a policy that")
        _p("is not actually armed — `curfew explain` will tell you which.")
    return 0


def cmd_explain(args) -> int:
    cfg = config.load(os.getcwd())
    env = dict(os.environ)
    _p("config:   {}".format(cfg.get("_config_path") or "(defaults only)"))
    if cfg.get("_config_error"):
        _p("WARNING:  config did not parse, running on defaults — {}".format(
            cfg["_config_error"]))
    _p("enabled:  {}".format(cfg.get("enabled", True)))
    _p("arms on:  {}".format(", ".join(cfg.get("arm_when_env") or []) or "(nothing)"))
    live = [n for n in (cfg.get("arm_when_env") or []) if env.get(n) == "1"]
    _p("armed now: {}".format("yes, via " + ", ".join(live) if live
                              else ("yes, always_on" if cfg.get("always_on") else "no")))
    _p("on error: {}".format(cfg.get("on_error")))
    _p("")
    _p("protected paths ({}):".format(len(cfg.get("protect") or [])))
    for r in cfg.get("protect") or []:
        _p("  {:<26} {}".format(r, "glob" if globs.is_glob(r) else "substring"))
    _p("")
    _p("denied shell fragments ({}): {}".format(
        len(cfg.get("shell_deny") or []),
        ", ".join((cfg.get("shell_deny") or [])[:14]) + " ..."))
    conn = cfg.get("connectors") or {}
    _p("")
    _p("connector prefixes denied: {}".format(", ".join(conn.get("deny_prefixes") or [])))
    _p("connector tools allowed:   {}".format(", ".join(conn.get("allow") or []) or "(none)"))
    _p("")
    _p("phases: {}".format(", ".join((cfg.get("phases") or {}).keys()) or "(none)"))
    _p("tier 1 never: {}".format(", ".join((cfg.get("tiers") or {}).get("never_tier1") or [])))
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        prog="curfew",
        description="Write-time policy for coding agents that run unattended.")
    sub = ap.add_subparsers(dest="cmd")

    p = sub.add_parser("init", help="write a starter .curfew.json")
    p.add_argument("path", nargs="?")
    p.add_argument("--force", action="store_true")
    p.set_defaults(func=cmd_init)

    p = sub.add_parser("check", help="ask the policy about one call")
    p.add_argument("--write", help="a path the agent would write")
    p.add_argument("--bash", help="a shell command the agent would run")
    p.add_argument("--tool", help="any other tool name")
    p.add_argument("--args", help="JSON args for --tool")
    p.add_argument("--phase", help="pretend CURFEW_PHASE is this")
    p.add_argument("--day", action="store_true", help="pretend it is a daylight run")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_check)

    p = sub.add_parser("tier", help="auto-merge, or stop at a pull request")
    p.add_argument("paths", nargs="*")
    p.add_argument("--base", default="HEAD")
    p.add_argument("--kind")
    p.add_argument("--lines", type=int)
    p.add_argument("--claimed", type=int, help="the tier the run claimed for itself")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_tier)

    p = sub.add_parser("report", help="what happened, in plain english")
    p.add_argument("--since", help="ISO timestamp")
    p.add_argument("--tail", type=int, default=8)
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("explain", help="which rules are live and where from")
    p.set_defaults(func=cmd_explain)
    return ap


def main(argv=None) -> int:
    ap = build_parser()
    args = ap.parse_args(argv)
    if not getattr(args, "func", None):
        ap.print_help()
        return 1
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
