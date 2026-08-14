# Changelog

## 0.1.0 — first cut

Extracted from a private hook that had been running nightly against a real
repo. Generalized: paths, shell verbs, connector prefixes, phases and tiers all
move into `.curfew.json` instead of being hard-coded to one person's machine.

Added on the way out: `curfew check` so the policy can be interrogated without
running anything, `curfew explain` so you can see which rules are actually
live, and a deliberate fail-closed path — the original printed a traceback and
therefore allowed the call.
