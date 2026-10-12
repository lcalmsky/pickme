import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.py"


def run(*args, env):
    return subprocess.run([sys.executable, str(CONFIG), *args],
                          capture_output=True, text=True, env=env)


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.path = self.tmp / "nested" / "config.json"
        self.env = dict(os.environ)
        self.env["PICKME_CONFIG"] = str(self.path)
        self.env.pop("XDG_CONFIG_HOME", None)

    def run_config(self, *args, env=None):
        return run(*args, env=env or self.env)

    def test_path_prints_the_config_path(self):
        proc = self.run_config("path")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), str(self.path))

    def test_path_follows_xdg_config_home(self):
        env = dict(self.env)
        del env["PICKME_CONFIG"]
        env["XDG_CONFIG_HOME"] = str(self.tmp / "xdg")
        proc = self.run_config("path", env=env)
        self.assertEqual(proc.stdout.strip(), str(self.tmp / "xdg" / "pickme" / "config.json"))

    def test_get_before_set_is_unset(self):
        proc = self.run_config("get", "open")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "unset")

    def test_set_then_get(self):
        saved = self.run_config("set", "open", "browser")
        self.assertEqual(saved.returncode, 0)
        self.assertEqual(saved.stdout.strip(), "saved: open=browser")
        self.assertEqual(self.run_config("get", "open").stdout.strip(), "browser")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"open": "browser"})

    def test_set_creates_the_directory_and_leaves_no_temporary_file(self):
        self.run_config("set", "open", "orca")
        self.assertEqual(sorted(p.name for p in self.path.parent.iterdir()), ["config.json"])

    def test_set_overwrites_the_value_and_keeps_unknown_keys(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text('{"open": "none", "future": "x"}', encoding="utf-8")
        self.run_config("set", "open", "auto")
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")),
                         {"open": "auto", "future": "x"})

    def test_unknown_key_exits_two(self):
        for args in (("get", "decider"), ("set", "decider", "none")):
            proc = self.run_config(*args)
            self.assertEqual(proc.returncode, 2, args)
            self.assertTrue(proc.stderr.startswith("error: "), proc.stderr)

    def test_unknown_value_exits_two_and_is_not_saved(self):
        proc = self.run_config("set", "open", "safari")
        self.assertEqual(proc.returncode, 2)
        self.assertTrue(proc.stderr.startswith("error: "), proc.stderr)
        self.assertFalse(self.path.exists())

    def test_broken_json_exits_two(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("{not json", encoding="utf-8")
        for args in (("get", "open"), ("set", "open", "none")):
            proc = self.run_config(*args)
            self.assertEqual(proc.returncode, 2, args)
            self.assertIn(str(self.path), proc.stderr)

    def test_missing_subcommand_exits_two(self):
        self.assertEqual(self.run_config().returncode, 2)


if __name__ == "__main__":
    unittest.main()
