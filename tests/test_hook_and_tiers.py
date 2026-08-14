import json
import os
import subprocess
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from curfew import audit, config, globs, hook, tiers  # noqa: E402


class Globs(unittest.TestCase):
    def test_star_does_not_cross_a_slash(self):
        self.assertTrue(globs.matches("src/a.py", "src/*.py"))
        self.assertFalse(globs.matches("src/deep/a.py", "src/*.py"))

    def test_doublestar_crosses(self):
        self.assertTrue(globs.matches("src/deep/a.py", "src/**/*.py"))
        self.assertTrue(globs.matches("a.py", "**/*.py"))

    def test_fragment_is_substring_and_case_insensitive(self):
        self.assertTrue(globs.matches("backup/OLD.env.bak", ".ENV"))

    def test_first_match_names_the_rule(self):
        self.assertEqual(globs.first_match("k.pem", ["x", "**/*.pem"]), "**/*.pem")


class Arming(unittest.TestCase):
    def test_not_armed_means_no_opinion(self):
        cfg = config.load_profile()
        self.assertFalse(config.armed(cfg, {}))

    def test_arms_on_the_variable(self):
        cfg = config.load_profile()
        self.assertTrue(config.armed(cfg, {"UNATTENDED_RUN": "1"}))

    def test_always_on(self):
        cfg = config.load_profile()
        cfg["always_on"] = True
        self.assertTrue(config.armed(cfg, {}))

    def test_disabled_beats_everything(self):
        cfg = config.load_profile()
        cfg["always_on"] = True
        cfg["enabled"] = False
        self.assertFalse(config.armed(cfg, {"UNATTENDED_RUN": "1"}))


class HookCore(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()

    def test_unarmed_hook_says_nothing(self):
        out = hook.run({"tool_name": "Write", "tool_input": {"file_path": ".env"},
                        "cwd": self.dir}, {})
        self.assertEqual(out, {})

    def test_armed_hook_denies_and_explains(self):
        out = hook.run({"tool_name": "Write", "tool_input": {"file_path": ".env"},
                        "cwd": self.dir}, {"UNATTENDED_RUN": "1"})
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn(".env", out["hookSpecificOutput"]["permissionDecisionReason"])

    def test_the_audit_log_is_written_by_the_guard(self):
        env = {"UNATTENDED_RUN": "1"}
        hook.run({"tool_name": "Write", "tool_input": {"file_path": ".env"},
                  "cwd": self.dir}, env)
        hook.run({"tool_name": "Write", "tool_input": {"file_path": "src/ok.py"},
                  "cwd": self.dir}, env)
        rows = audit.read(config.load_profile(), self.dir)
        self.assertEqual(len(rows), 2)
        s = audit.summarize(rows)
        self.assertEqual((s["allowed"], s["denied"]), (1, 1))


class HookProcess(unittest.TestCase):
    """The real thing: pipe JSON in, read JSON out, check the exit code."""

    def _run(self, payload, env=None):
        e = dict(os.environ)
        e["PYTHONPATH"] = ROOT
        e.update(env or {})
        p = subprocess.run([sys.executable, "-m", "curfew.hook"],
                           input=json.dumps(payload), capture_output=True,
                           text=True, cwd=ROOT, env=e)
        return p

    def test_denies_a_real_payload(self):
        p = self._run({"tool_name": "Bash",
                       "tool_input": {"command": "rm -rf /tmp/whatever"},
                       "cwd": ROOT}, {"UNATTENDED_RUN": "1"})
        self.assertEqual(p.returncode, 0)
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")

    def test_malformed_stdin_does_not_wedge_the_session(self):
        e = dict(os.environ)
        e["PYTHONPATH"] = ROOT
        e["UNATTENDED_RUN"] = "1"
        p = subprocess.run([sys.executable, "-m", "curfew.hook"],
                           input="not json at all", capture_output=True,
                           text=True, cwd=ROOT, env=e)
        self.assertEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout), {})

    def test_a_broken_policy_denies_rather_than_waves_through(self):
        # Printing nothing is read as ALLOW, so this is the failure mode that
        # matters. Force an exception and check which way it falls.
        e = dict(os.environ)
        e["PYTHONPATH"] = ROOT
        e["UNATTENDED_RUN"] = "1"
        e["CURFEW_FORCE_ERROR"] = "1"
        script = (
            "import json,sys,os;"
            "sys.path.insert(0, %r);"
            "from curfew import hook, policy;"
            "policy.decide = lambda *a, **k: (_ for _ in ()).throw(RuntimeError('boom'));"
            "hook.main()" % ROOT)
        p = subprocess.run([sys.executable, "-c", script],
                           input=json.dumps({"tool_name": "Write",
                                             "tool_input": {"file_path": "x.py"},
                                             "cwd": ROOT}),
                           capture_output=True, text=True, cwd=ROOT, env=e)
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
        self.assertIn("refused it rather", out["hookSpecificOutput"]["permissionDecisionReason"])


class Tiers(unittest.TestCase):
    def setUp(self):
        self.cfg = config.load_profile()
        self.cfg["tiers"]["tier1_max_lines"] = 120

    def test_a_typo_fix_in_a_source_file_may_auto_merge(self):
        r = tiers.tier_for(["src/util.py"], self.cfg, kind="typo", lines_changed=3)
        self.assertTrue(r.may_auto_merge)

    def test_anything_a_stranger_opens_stops_at_a_pr(self):
        r = tiers.tier_for(["public/index.html"], self.cfg, kind="typo", lines_changed=2)
        self.assertFalse(r.may_auto_merge)
        self.assertIn("never_tier1", r.why)

    def test_a_big_change_stops_even_if_it_is_routine(self):
        r = tiers.tier_for(["src/util.py"], self.cfg, kind="typo", lines_changed=900)
        self.assertFalse(r.may_auto_merge)

    def test_undeclared_kind_stops(self):
        self.assertFalse(tiers.tier_for(["src/util.py"], self.cfg).may_auto_merge)

    def test_the_claim_is_re_derived_not_believed(self):
        r = tiers.tier_for(["public/index.html"], self.cfg, kind="typo", lines_changed=1)
        warn = tiers.check_claim(1, r)
        self.assertIsNotNone(warn)
        self.assertIn("claimed tier 1", warn)

    def test_agreement_is_silent(self):
        r = tiers.tier_for(["src/util.py"], self.cfg, kind="docs", lines_changed=4)
        self.assertIsNone(tiers.check_claim(1, r))


class ConfigMerge(unittest.TestCase):
    def test_repo_config_adds_to_the_defaults_rather_than_replacing(self):
        base = config.load_profile()
        merged = config.merge(base, {"protect": ["secrets/**"]})
        self.assertIn("secrets/**", merged["protect"])
        self.assertIn(".env", merged["protect"])

    def test_removing_a_default_takes_saying_so(self):
        base = config.load_profile()
        merged = config.merge(base, {"protect_remove": [".claude/"]})
        self.assertNotIn(".claude/", merged["protect"])
        self.assertIn(".env", merged["protect"])

    def test_a_config_that_does_not_parse_keeps_the_defaults(self):
        d = tempfile.mkdtemp()
        with open(os.path.join(d, ".curfew.json"), "w") as f:
            f.write("{ this is not json")
        cfg = config.load(d)
        self.assertIn(".env", cfg["protect"])
        self.assertIn("_config_error", cfg)


if __name__ == "__main__":
    unittest.main()


class InstalledTheDocumentedWay(unittest.TestCase):
    """install.sh wires an absolute path to hook.py, so it runs as a loose
    script with no parent package. A relative import there raises before
    anything prints, and an empty stdout is read as ALLOW — the guard would sit
    in settings.json permitting everything. Test the documented invocation,
    not the convenient one."""

    def test_running_the_file_directly_still_denies(self):
        e = dict(os.environ)
        e["UNATTENDED_RUN"] = "1"
        e.pop("PYTHONPATH", None)
        p = subprocess.run(
            [sys.executable, os.path.join(ROOT, "curfew", "hook.py")],
            input=json.dumps({"tool_name": "Write",
                              "tool_input": {"file_path": ".env"},
                              "cwd": ROOT}),
            capture_output=True, text=True, cwd=tempfile.mkdtemp(), env=e)
        self.assertEqual(p.stderr, "")
        out = json.loads(p.stdout)
        self.assertEqual(out["hookSpecificOutput"]["permissionDecision"], "deny")
