"""`tools/check.py` and a machine's heavy-process gate: through it where it is, without it else.

A stand-in gate speaks the gate's protocol 1 as far as `check.py` uses it: its
`heavy-gate.json` names a command line, its `run` replaces `{k}` in COMMAND with the
K it grants and exits with COMMAND's code, or exits 75 without starting COMMAND when
it finds no room. The real gate is never started here.
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import check
import parallel_tests

GATE = """
import json, subprocess, sys
from pathlib import Path
here = Path(__file__).parent
with (here / "gate-calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
if (here / "no-room").exists():
    raise SystemExit(75)
command = sys.argv[sys.argv.index("--") + 1 :]
raise SystemExit(subprocess.call([word.replace("{k}", "3") for word in command]))
"""

RUNNER = """
import json, sys
from pathlib import Path
with (Path(__file__).parent / "runner-calls.jsonl").open("a", encoding="utf-8") as log:
    log.write(json.dumps(sys.argv[1:]) + "\\n")
"""


class CheckHeavyGateTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="check heavy gate ")
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name).resolve()
        self.bin = self.folder / "bin"
        self.bin.mkdir()
        (self.bin / "gate.py").write_text(GATE, encoding="utf-8")
        self.runner = self.folder / "runner.py"
        self.runner.write_text(RUNNER, encoding="utf-8")
        self.empty = self.folder / "empty"
        self.empty.mkdir()

    def install(self, document):
        text = document if isinstance(document, str) else json.dumps(document)
        (self.bin / "heavy-gate.json").write_text(text, encoding="utf-8")

    def install_gate(self):
        self.install({"protocol": 1, "argv": [sys.executable, str(self.bin / "gate.py")]})

    def calls(self, name):
        path = (self.bin if name == "gate" else self.folder) / f"{name}-calls.jsonl"
        if not path.is_file():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def check(self, *other_gates):
        """Run `check.main` with the stand-in runner as the test suite and this PATH."""
        tests = str(self.runner)
        output = io.StringIO()
        path = os.pathsep.join((str(self.empty), str(self.bin)))
        with (
            patch.object(check, "TESTS", tests),
            patch.object(check, "GATES", ([tests], *other_gates)),
            patch.dict(os.environ, {"PATH": path}),
            contextlib.redirect_stdout(output),
        ):
            code = check.main()
        return code, output.getvalue()

    def test_without_a_gate_the_suite_runs_directly_with_the_runners_default(self):
        for environment in ({"PATH": str(self.empty)}, {"PATH": ""}, {}):
            with self.subTest(environment=environment):
                self.assertEqual(
                    [sys.executable, check.TESTS], check.command([check.TESTS], environment)
                )

    def test_the_suite_runs_under_the_gate_and_gets_the_k_it_grants(self):
        self.install_gate()
        ruff_stand_in = ["-c", "pass"]
        code, output = self.check(ruff_stand_in)
        self.assertEqual(0, code, output)
        # K reaches the runner as its worker count, and nothing else of its line changed.
        self.assertEqual([["--jobs=3"]], self.calls("runner"))
        [gate_call] = self.calls("gate")
        separator = gate_call.index("--")
        self.assertEqual(
            ["run", "--kind", "tests", "--want", str(parallel_tests.default_jobs())],
            gate_call[:separator],
        )
        self.assertEqual(
            [sys.executable, str(self.runner), "--jobs={k}"], gate_call[separator + 1 :]
        )
        self.assertIn("through the heavy-process gate, kind tests", output)
        # Only the test suite is heavy: the lint gates never reach the gate.
        self.assertEqual(1, len(self.calls("gate")))

    def test_no_room_ends_the_check_with_the_gates_code_and_says_how_to_wait(self):
        self.install_gate()
        (self.bin / "no-room").write_text("", encoding="utf-8")
        marker = self.folder / "later-gate-ran"
        code, output = self.check(["-c", f"open({str(marker)!r}, 'w').close()"])
        self.assertEqual(check.HEAVY_GATE_NO_ROOM, code)
        self.assertEqual([], self.calls("runner"))
        self.assertFalse(marker.exists(), "a refused suite must end the check")
        self.assertIn("HEAVY_GATE_WAIT=1", output)

    def test_a_failing_suite_under_the_gate_fails_the_check(self):
        self.install_gate()
        self.runner.write_text(RUNNER + "raise SystemExit(1)\n", encoding="utf-8")
        code, output = self.check()
        self.assertEqual(1, code)
        self.assertNotIn("HEAVY_GATE_WAIT", output)

    def test_an_unusable_gate_entry_runs_the_suite_without_the_gate(self):
        unusable = {
            "not json": "{",
            "another protocol": {"protocol": 2, "argv": [sys.executable, "gate.py"]},
            "no command line": {"protocol": 1, "argv": []},
            "not an object": [1],
        }
        environment = {"PATH": str(self.bin)}
        for name, document in unusable.items():
            with self.subTest(name):
                self.install(document)
                output = io.StringIO()
                with contextlib.redirect_stdout(output):
                    argv = check.command([check.TESTS], environment)
                self.assertEqual([sys.executable, check.TESTS], argv)
                self.assertIn("tests run without the gate", output.getvalue())

    def test_the_first_gate_entry_on_path_decides(self):
        self.install_gate()
        second = self.folder / "second"
        second.mkdir()
        (second / "heavy-gate.json").write_text('{"protocol": 1, "argv": ["other"]}', "utf-8")
        environment = {"PATH": os.pathsep.join((str(self.empty), str(self.bin), str(second)))}
        argv = check.command([check.TESTS], environment)
        self.assertEqual([sys.executable, str(self.bin / "gate.py"), "run"], argv[:3])

    def test_the_rules_file_declares_the_kind_check_runs_the_suite_as(self):
        rules = tomllib.loads((ROOT / ".heavy-gate.toml").read_text(encoding="utf-8"))
        self.assertEqual(1, rules["schema"])
        [kind] = [kind for kind in rules["kind"] if kind["name"] == check.HEAVY_GATE_KIND]
        # The gate fills `{k}` only for a kind with workers, and clamps the want
        # check.py asks for into [min, max]: max must reach the runner's own ceiling.
        self.assertLessEqual(kind["workers"]["min"], 1)
        self.assertGreaterEqual(kind["workers"]["max"], parallel_tests.MAX_WORKERS)
        # The refusal hook knows a hand-started runner by this prefix and offers it
        # back with the worker count the runner takes.
        self.assertIn(f"python {check.TESTS}", kind["commands"])
        self.assertEqual("--jobs={k}", kind["suggest"])
        self.assertFalse(kind.get("self_gated", False))


if __name__ == "__main__":
    unittest.main()
