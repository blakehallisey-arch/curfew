"""The decision.

One function, `decide()`, takes a tool call and returns allow or deny with a
reason. Everything else in this package is loading, logging, or printing.

The reason string is not an afterthought. It goes straight back to the agent
that tried the call, and it is the only thing standing between "the rail got
blocked and stopped" and "the rail got blocked, understood why, recorded the
item as blocked, and moved on to the next one." Every deny here says what
matched, why the rule exists, and what to do instead.
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from . import globs

WRITE_TOOLS = ("Write", "Edit", "MultiEdit", "NotebookEdit")

# Tools whose whole job is to look at something. A guard that blocks reading is
# a guard that gets switched off by lunchtime.
READ_TOOLS = ("Read", "Glob", "Grep", "WebFetch", "WebSearch", "TodoWrite",
              "NotebookRead", "ListMcpResources", "ReadMcpResource")


class Decision(object):
    __slots__ = ("action", "reason", "rule", "category")

    def __init__(self, action, reason="", rule=None, category=None):
        self.action = action          # "allow" | "deny"
        self.reason = reason
        self.rule = rule              # the literal rule that matched
        self.category = category      # connector | phase | path | shell | merge

    @property
    def denied(self) -> bool:
        return self.action == "deny"

    def as_dict(self) -> Dict[str, Any]:
        return {"action": self.action, "reason": self.reason,
                "rule": self.rule, "category": self.category}

    def __repr__(self):
        return "<Decision {} {}>".format(self.action, self.category or "-")


ALLOW = Decision("allow")


def _deny(reason: str, rule: Optional[str], category: str) -> Decision:
    return Decision("deny", reason, rule, category)


# ── shell helpers ───────────────────────────────────────────────────────────

def is_read_only(cmd: str, cfg: Dict[str, Any]) -> bool:
    """Narrow on purpose.

    The command must START with a known read-only program AND contain no way to
    chain, redirect, or hand off. Anything with `>` `|` `;` `&&` `||` a
    backtick, `$(` or a newline is not a read, whatever it starts with.

    That is what keeps `cat secrets.json` allowed and `cat secrets.json > x`
    denied without anyone having to parse shell to guess intent. Parsing shell
    to guess intent is how you get a guard that is clever on Tuesday and wrong
    on the night it matters.
    """
    c = (cmd or "").strip()
    if not c:
        return False
    programs = cfg.get("read_only_programs") or []
    chains = cfg.get("chain_characters") or []
    if not any(c.startswith(p) for p in programs):
        return False
    return not any(ch in c for ch in chains)


_TOKEN = re.compile(r"[^\s'\"=,()]+")


def path_tokens(cmd: str) -> List[str]:
    """Every token in a shell command that could be a path.

    Deliberately crude. We are not trying to understand the command, only to
    notice that a protected filename is somewhere inside it. Writing through an
    interpreter, a heredoc, `sed -i`, `tee` or a redirect is the same act as
    writing through the editor, and none of those contain a `>`:

        python3 - <<EOF
        open('.env','w').write(...)
        EOF

    A redirect check never sees that. A "does this command name a protected
    file at all" check does.
    """
    return [t for t in _TOKEN.findall(cmd or "") if "/" in t or "." in t]


def _pipes_download_to_shell(low: str) -> bool:
    """`curl ... | sh`.

    Written as a pair test rather than a literal, because the real command
    always has a URL in the middle — the substring "curl | sh" never matches
    `curl -fsSL https://x.sh | bash`, which is the actual shape of it.
    """
    if "curl" not in low and "wget" not in low:
        return False
    return any(s in low for s in ("| sh", "|sh", "| bash", "|bash",
                                 "| zsh", "|zsh", "| python", "|python"))


def _merges_default_branch(low: str, branch: str) -> bool:
    """Does this command put code on the default branch?

    Plain token comparison, not a regex, and not a substring test on the branch
    name. `"main" in cmd` would block `git push -u origin curfew/main-nav-fix`,
    which is the exact branch an unattended rail is supposed to push. Only a
    refspec that IS the branch counts.
    """
    if "gh pr merge" in low:
        return True
    if "gh api" in low and "/merge" in low:
        return True
    if re.search(r"\bgit\b.*\bmerge\b", low) and " --abort" not in low:
        return True
    if "git push" in low:
        for tok in low.split():
            if tok == branch or tok.endswith(":" + branch) or tok.endswith("/" + branch):
                return True
    return False


# ── the decision ────────────────────────────────────────────────────────────

def decide(tool: str, args: Dict[str, Any], cfg: Dict[str, Any],
           env: Dict[str, str]) -> Decision:
    tool = tool or ""
    args = args or {}

    # 1. CONNECTORS, first and for every phase.
    #
    # Deny by PREFIX, never by listing tool names. This is the rule that gets
    # written down wrong most often and it is the one that matters most: a
    # connector tool that ships in some future release must arrive DENIED, not
    # allowed. The entire failure mode here is a capability nobody remembered
    # to add to a list. An unattended run has no business creating a calendar
    # event, sending a message, or answering an invitation, and "we did not
    # know that tool existed yet" is not a defence you get to use afterwards.
    conn = cfg.get("connectors") or {}
    prefixes = tuple(conn.get("deny_prefixes") or ())
    allow = tuple(conn.get("allow") or ())
    if prefixes and tool.startswith(prefixes) and tool not in allow:
        return _deny(
            "curfew: this run cannot call {}. It is unattended, so it does not "
            "touch live connected services — mail, calendars, drives, "
            "scheduled jobs. Read-only connector tools can be added to "
            "connectors.allow in .curfew.json; anything that creates, sends or "
            "changes should not be. If the work genuinely needs this, stop and "
            "record the item as blocked so a human can do it with their eyes "
            "open.".format(tool),
            tool, "connector")

    # 2. PHASE. A phase is a run that has one job and should be physically
    # unable to do a second one. The classic is a planning pass whose prompt
    # says "you are not changing anything in this run" — and which, on its
    # first real night, built a feature and merged three pull requests. The
    # prompt was a request. This is the rule.
    phase_name = env.get("CURFEW_PHASE") or ""
    phase = (cfg.get("phases") or {}).get(phase_name)
    if phase:
        why = phase.get("reason") or "This phase is restricted."
        if tool in WRITE_TOOLS:
            target = str(args.get("file_path") or args.get("notebook_path") or "")
            allowed = phase.get("may_write") or []
            if not globs.first_match(target, allowed):
                return _deny(
                    "curfew: phase '{}' may not write {}. {} Allowed here: "
                    "{}".format(phase_name, target or "(no path)", why,
                                ", ".join(allowed) or "nothing"),
                    phase_name, "phase")
        if tool == "Bash" and phase.get("shell") == "read_only":
            cmd = str(args.get("command") or "")
            if not is_read_only(cmd, cfg):
                return _deny(
                    "curfew: phase '{}' allows read-only shell only. {} No "
                    "interpreters, no redirects, no git, no package managers. "
                    "Allowed starts: {}.".format(
                        phase_name, why,
                        ", ".join((cfg.get("read_only_programs") or [])[:10])),
                    phase_name, "phase")

    # 3. PROTECTED PATHS, through the editor.
    if tool in WRITE_TOOLS:
        target = str(args.get("file_path") or args.get("notebook_path") or "")
        hit = globs.first_match(target, cfg.get("protect") or [])
        if hit:
            return _deny(
                "curfew: this run is not allowed to write {}. It matched the "
                "protected rule '{}'. Protected paths are secrets, live "
                "personal data, and the rules the run itself operates under — "
                "a process that can rewrite its own guardrails does not have "
                "guardrails, it has suggestions. This is a hard rule, not a "
                "preference. Record the item as blocked, say this is why, and "
                "move on.".format(target, hit),
                hit, "path")

    if tool != "Bash":
        return ALLOW

    cmd = str(args.get("command") or "")
    low = cmd.lower()

    # 4. Downloading a script and running it.
    if _pipes_download_to_shell(low):
        return _deny(
            "curfew: downloading a script and piping it into a shell runs "
            "whatever that server sends, right now, with nobody watching. This "
            "run does not do that. Fetch it, let a human read it, then run it. "
            "Record the item as blocked.",
            "curl|sh", "shell")

    # 5. DAY MODE. Same rail, same queue, but a human is at the desk and may be
    # in these same files. The one thing daylight must not do is put code on the
    # default branch behind their back. Enforced here rather than asked for in a
    # prompt, for the same reason as everything else on this page.
    day = cfg.get("day_mode") or {}
    if day.get("env") and env.get(day["env"]) == "1":
        branch = (day.get("default_branch") or "main").lower()
        if _merges_default_branch(low, branch):
            return _deny(
                "curfew: {} Command was: {}".format(
                    day.get("reason") or "Daylight runs do not merge.",
                    cmd.strip()[:200]),
                "day_mode", "merge")

    # 6. Verbs that destroy or send.
    for frag in cfg.get("shell_deny") or []:
        if frag.lower() in low:
            return _deny(
                "curfew: this run is not allowed to run '{}'. Deletes should be "
                "recoverable, force-pushes and hard resets destroy work git "
                "cannot get back, package installs execute third-party setup "
                "scripts on this machine with nobody watching, and nothing "
                "outbound leaves an unattended rail. If the work genuinely "
                "needs it, record the item as blocked and say so.".format(frag),
                frag, "shell")

    # 7. A protected path NAMED ANYWHERE in a shell command — not just after a
    # redirect. See path_tokens() for why. This is blunt: it also blocks
    # `grep .env config/`, which is a real cost and the right trade. Reading is
    # what the Read tool is for, and the Read tool is not restricted.
    if not is_read_only(cmd, cfg):
        protect = cfg.get("protect") or []
        for tok in path_tokens(cmd):
            hit = globs.first_match(tok, protect)
            if hit:
                return _deny(
                    "curfew: that shell command names a protected path ('{}' "
                    "matched rule '{}'). Writing through an interpreter, a "
                    "heredoc, sed, tee or a redirect is the same act as writing "
                    "through the editor, so shell commands naming these paths "
                    "are denied outright rather than guessed about. Use the "
                    "Read tool if you only need to look. Otherwise record the "
                    "item as blocked.".format(tok, hit),
                    hit, "path")
        # Substring rules can name a thing that is not a path-shaped token —
        # `crontab`, `launchagents`. Catch those against the whole command.
        for rule in protect:
            if not globs.is_glob(rule) and rule.lower() in low:
                return _deny(
                    "curfew: that shell command names a protected target "
                    "('{}'). Denied outright. Record the item as blocked and "
                    "say this is why.".format(rule),
                    rule, "path")

    return ALLOW
