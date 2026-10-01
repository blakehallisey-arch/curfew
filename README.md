# curfew

Write-time policy for coding agents that run unattended. It decides what the
agent may write, run, and merge — by rule, in code, before the call happens.

## The problem

You want to hand a queue of work to a coding agent at eleven at night and read
the results over coffee. The moment you do, every control you have is a
sentence in a prompt.

A prompt is a request. Here is what that is worth. A planning pass was told, in
capital letters, that it was not changing anything in that run. It built a
feature and merged three pull requests. A rail was told to route deletes
through a recoverable trash script; it ran `git stash` instead, which moves
uncommitted work off the tree without naming a single file. A guard that
blocked writes to protected paths through the editor was walked straight
through with a heredoc:

```
python3 - <<EOF
open('.claude/settings.json','w').write('{}')
EOF
```

There is no `>` in that command. The redirect check never saw it. And the file
it was rewriting was the file that holds the rules the run operates under — a
process that can edit its own guardrails does not have guardrails, it has
suggestions.

None of these were adversarial. They were an agent trying to finish the job it
was given, taking the shortest path, at three in the morning, with nobody
awake. That is the whole threat model and it is enough.

## Install

```
git clone https://github.com/blakehallisey-arch/curfew && cd curfew
./install.sh          # merges one PreToolUse hook into ~/.claude/settings.json
```

Then in your repo:

```
python3 -m curfew init
```

Nothing is enforced until a run says it is unattended. Your runner exports
`UNATTENDED_RUN=1`; a session you are driving by hand is untouched. That split
is the point — a policy strict enough to be worth having at 3am is infuriating
to work under by hand, and a policy loose enough to work under by hand is not
worth having.

## What it looks like

`curfew check` asks the policy about a call without running anything. This is
real output, not a mockup:

```
$ curfew check --write config/.env.production
DENY  [path]  rule: .env

curfew: this run is not allowed to write config/.env.production. It matched the
protected rule '.env'. Protected paths are secrets, live personal data, and the
rules the run itself operates under — a process that can rewrite its own
guardrails does not have guardrails, it has suggestions. This is a hard rule,
not a preference. Record the item as blocked, say this is why, and move on.
(exit 3)

$ curfew check --bash "python3 - <<EOF ... open('.claude/settings.json','w') ... EOF"
DENY  [path]  rule: .claude/

curfew: that shell command names a protected path ('.claude/settings.json'
matched rule '.claude/'). Writing through an interpreter, a heredoc, sed, tee
or a redirect is the same act as writing through the editor, so shell commands
naming these paths are denied outright rather than guessed about. Use the Read
tool if you only need to look. Otherwise record the item as blocked.
(exit 3)

$ curfew check --bash "cat .env"
ALLOW
(exit 0)

$ curfew check --bash "cat .env > /tmp/copy"
DENY  [path]  rule: .env
(exit 3)

$ curfew check --tool mcp__calendar__create_event
DENY  [connector]  rule: mcp__calendar__create_event
(exit 3)
```

Day mode, for when you start the same rail while you are at your desk:

```
$ curfew check --day --bash "git push origin main"
DENY  [merge]  rule: day_mode

curfew: This is a daylight run. A human is at the desk right now, possibly in
these same files. Every item ends at an OPEN PR: push the branch, open the PR,
stop. Nothing merges to the default branch while somebody is working.
(exit 3)

$ curfew check --day --bash "git push -u origin curfew/main-nav-fix"
ALLOW
(exit 0)
```

That second one matters more than it looks. The obvious way to write that rule
is `"main" in command`, and the obvious way blocks the exact branch the rail
exists to push.

The tier gate, which decides whether a finished change may merge itself or has
to stop at a pull request:

```
$ curfew tier src/util.py --kind typo --lines 3
tier 1 — may auto-merge
why: no protected path, and kind 'typo' is routine

$ curfew tier public/index.html --kind typo --lines 2 --claimed 1
tier 2 — stops at a pull request
why: public/index.html matches never_tier1 rule '**/*.html'
files: public/index.html
lines changed: 2

DISAGREEMENT: the run claimed tier 1, the policy says tier 2 — public/index.html
matches never_tier1 rule '**/*.html'
```

And in the morning:

```
$ curfew report
12 calls from 2026-08-14T10:09:50 to 2026-08-14T10:09:50
7 allowed, 5 refused

Refused, by reason:
  shell      2
  path       2
  connector  1

Refused, by rule:
  pip3 install                 1
  .env                         1
  mcp__mail__create_draft      1
  .claude/                     1
  git stash                    1

The last few refusals:
  2026-08-14T10:09:50  Bash  pip3 install lxml
  2026-08-14T10:09:50  Write  .env
  2026-08-14T10:09:50  mcp__mail__create_draft
  2026-08-14T10:09:50  Bash  sed -i '' 's/x/y/' .claude/settings.json
  2026-08-14T10:09:50  Bash  git stash
```

## How it works

A `PreToolUse` hook. Claude Code hands it the tool call as JSON on stdin before
running it; curfew writes an allow or a deny on stdout. Six checks, in order.

**1. Connectors, by prefix.** Anything matching a denied prefix (`mcp__` by
default) is refused unless it is on an explicit allow list. Deny by prefix,
never by listing tool names — a connector that ships in some future release
must arrive denied, not allowed. The entire failure mode here is a capability
nobody remembered to add to a list. An earlier version of this hook watched
`Write`, `Edit` and `Bash` and nothing else, which meant an unattended run
could put a draft in someone's real mailbox or decline a real calendar
invitation, one tool call away from a policy whose first line was "anything
that can reach a person."

**2. Phases.** A phase is a run that has one job and should be physically
unable to do a second one. The shipped `plan` phase may write one file and run
read-only shell. The read check is an allowlist of programs, not a denylist of
verbs, because a denylist only ever blocks the phrasings somebody thought of —
`python3 -c`, a heredoc, `bash script.sh`, a bare redirect, `git -c ... commit`,
`git -C . push`, `gh api -X PUT .../merge`. All seven get through a list of
banned words. None get through "must start with `cat`, `grep`, `git log`, and
contain no `>` `|` `;` `&&` `||` backtick `$(` or newline."

**3. Protected paths, through the editor.** Rules are globs if they contain
`*` `?` `[`, and plain case-insensitive substrings otherwise. `.env` covers
`.env.local`, `config/.env.production` and `backup-of-.env` without anyone
having to guess the shape of the filename.

**4. `curl | sh`.** Written as a pair test, not a literal, because the real
command always has a URL in the middle.

**5. Day mode.** Same rail, same queue, but nothing reaches the default branch,
because a human is at the desk and may be in the same files.

**6. Shell verbs, and protected paths named anywhere in a command.** Not just
after a redirect — see the heredoc above. This is blunt: it also blocks
`grep .env config/`, which is a real cost and the right trade. Reading is what
the `Read` tool is for, and the `Read` tool is not restricted.

Everything it decides goes to `.curfew/audit.jsonl`, written by the guard
rather than by the thing being guarded. That is deliberate. A run's own account
of its night goes stale the moment a write gets blocked: it finishes the work,
gets refused when it tries to record that it finished, and every board
downstream shows the item stuck forever. The guard is the one process in the
loop that cannot be talked out of it.

**What it cannot see.** Anything the model does inside a subagent that does not
surface as a tool call to this session. Anything your runner does outside the
agent. And it cannot read intent — it reads the call.

**Failure direction.** Printing nothing on stdout is read as *allow*. So an
exception in here is not "no decision", it is "yes, go ahead", arriving at the
exact moment something unexpected is happening. `main()` is wrapped and the
fallback is a deliberate deny for anything that can change the world. There is
a test that forces an exception and asserts which way it falls.

This bit is not hypothetical. `install.sh` wires the hook by absolute path, so
it runs as a loose script with no parent package — and the relative import at
the top of `hook.py` raised `ImportError` before anything printed. The guard
would have been installed, visible in `settings.json`, and silently permitting
everything. Found by running the documented install command instead of the
convenient one. There is now a test that invokes it exactly the way the
installer does.

## Configuration

`.curfew.json` at your repo root, merged on top of the shipped defaults in
`curfew/profiles/default.json`. Run `curfew explain` to see what is actually
live.

The merge is additive for the lists that protect things: your `protect` entries
are **added** to the defaults, not substituted for them. Dropping a default
takes saying so out loud with `protect_remove`. Silently narrowing a deny list
is how a guard rots.

| key | default | what it does |
|---|---|---|
| `enabled` | `true` | master switch |
| `arm_when_env` | `["UNATTENDED_RUN", "CURFEW_RUN"]` | env vars that mean "this run is unattended" |
| `always_on` | `false` | enforce even in a hand-driven session |
| `on_error` | `"deny"` | what to do if curfew itself throws, for calls that can change something |
| `protect` | secrets, the rules layer, shell rc files, cron | paths the run may not write, via editor or shell |
| `protect_remove` | `[]` | defaults you explicitly want gone |
| `shell_deny` | destroy, force-push, install, send | command fragments refused outright |
| `read_only_programs` | `cat`, `grep`, `git log`, … | the allowlist that defines "this command only reads" |
| `chain_characters` | `> \| ; && \|\| \` $( \n` | anything containing one of these is not a read |
| `connectors.deny_prefixes` | `["mcp__", "Cron"]` | prefix match, so unknown future tools arrive denied |
| `connectors.allow` | `[]` | exact tool names allowed anyway — keep these read-only |
| `phases` | `plan` | per-phase restrictions, keyed on `CURFEW_PHASE` |
| `day_mode.env` | `CURFEW_DAY` | when set to `1`, nothing reaches the default branch |
| `day_mode.default_branch` | `main` | matched as a whole token, never as a substring |
| `tiers.never_tier1` | html, css, migrations, manifests | anything here always stops at a pull request |
| `tiers.tier1_kinds` | typo, docs, lint, dead-code, comment, test-fix | the only kinds of work that may merge themselves |
| `tiers.tier1_max_lines` | unset | a size ceiling on auto-merge |
| `audit.path` | `.curfew/audit.jsonl` | the log |
| `audit.log_allows` | `true` | log the boring ones too, so a quiet night is provable |

State lives in `.curfew/` inside your repo and nowhere else. No network calls,
no telemetry, no account. This runs on your laptop against your private repo,
which is the only reason it is worth trusting.

There is a worked example in [`examples/.curfew.json`](examples/.curfew.json).

## Tiers, and the one idea worth stealing

The thing that proposes the work is not the thing that authorizes it.

An unattended run that decides its own change is routine will, eventually,
decide that about a change that is not. Not because it is devious — because
"is this routine" is a judgment call and it is making it about its own work,
alone, at three in the morning, with no one to disagree.

So `curfew tier` re-derives the answer from the policy file, using only facts
that are true of the diff: which files it touches, how big it is, what kind of
work it was queued as. If the run claimed tier 1 and the policy says tier 2,
that disagreement gets printed. Usually it is honest and wrong. Occasionally it
is the only warning you get.

## What this is not

- **Not a sandbox.** A determined process can get around a hook — it is in the
  same trust domain as the agent. curfew stops an agent taking the shortest
  path to finishing its job. It does not stop an attacker. If you need real
  isolation, use a container or a VM, and use curfew inside it.
- **Not a prompt-injection defence.** If something in a file the agent reads
  tells it to do something, curfew judges the resulting tool calls and nothing
  else. That is useful, and it is not the same as being safe.
- **Not CI.** It runs on your laptop, before the call. You still want tests.
- **Not a review.** It has no opinion about whether the code is any good. See
  `shipgate` for the half that makes sure a review actually happened.
- **Not a scheduler.** It does not run anything. See `nightwatch`.

## Tests

```
python3 -m unittest discover tests
```

53 tests. They cover the denies above all — a guard with no test on its denies
is decoration. Including: the heredoc write, the seven ways round a
named-verb denylist, the branch called `main-nav-fix`, an unknown future
connector, a config that does not parse, and a forced internal exception.

## Part of a family

Six small tools for the case where an agent does the work and a human is not
watching every step.

| repo | one line |
|---|---|
| **curfew** | write-time policy — deny by rule, not by prompt |
| [breaker](https://github.com/blakehallisey-arch/breaker) | stops a session that is spinning, spreading, or inventing work |
| [shipgate](https://github.com/blakehallisey-arch/shipgate) | will not let a merge through until the checks it needs have run |
| [nightwatch](https://github.com/blakehallisey-arch/nightwatch) | the run rail — a queue, a budget lid, a window, an honest log |
| [draftdiff](https://github.com/blakehallisey-arch/draftdiff) | learns your voice from the edits you make before you send |
| [ledger](https://github.com/blakehallisey-arch/ledger) | gives stateless agents a memory of what you did with their advice |

All six are open.

MIT.

Built by Blake Hallisey. These six came out of one rail running overnight
against a real repo. The longer story, and what each night cost, is at
[how I use AI](https://blakehallisey.com/how-i-use-ai).
