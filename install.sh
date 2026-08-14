#!/usr/bin/env bash
# Wire curfew into Claude Code as a PreToolUse hook.
#
# It backs up settings.json first, merges rather than overwrites, and is safe to
# run twice. Set CLAUDE_SETTINGS to point it somewhere else (the tests do).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SETTINGS="${CLAUDE_SETTINGS:-$HOME/.claude/settings.json}"
PY="${PYTHON:-python3}"

mkdir -p "$(dirname "$SETTINGS")"
[ -f "$SETTINGS" ] || echo '{}' > "$SETTINGS"
cp "$SETTINGS" "$SETTINGS.curfew-backup"

CMD="$PY $HERE/curfew/hook.py"

"$PY" - "$SETTINGS" "$CMD" <<'PYEOF'
import json, sys

path, cmd = sys.argv[1], sys.argv[2]
with open(path) as f:
    cfg = json.load(f)

hooks = cfg.setdefault("hooks", {})
pre = hooks.setdefault("PreToolUse", [])

for entry in pre:
    for h in entry.get("hooks", []):
        if "curfew/hook.py" in str(h.get("command", "")):
            h["command"] = cmd
            with open(path, "w") as f:
                json.dump(cfg, f, indent=2)
            print("already wired — refreshed the path, changed nothing else")
            sys.exit(0)

pre.append({"matcher": "*", "hooks": [{"type": "command", "command": cmd}]})
with open(path, "w") as f:
    json.dump(cfg, f, indent=2)
print("added one PreToolUse entry:")
print("  " + cmd)
print("left %d existing PreToolUse entr%s alone"
      % (len(pre) - 1, "y" if len(pre) - 1 == 1 else "ies"))
PYEOF

echo
echo "backup: $SETTINGS.curfew-backup"
echo
echo "Nothing is enforced yet. curfew only has an opinion when the run says it"
echo "is unattended. Have your runner do this before it starts a session:"
echo
echo "    UNATTENDED_RUN=1 claude -p \"...\""
echo
echo "Check it with:  $PY -m curfew explain"
