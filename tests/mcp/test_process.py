"""Process ownership and publication routing tests; no hosted service is used."""

import hashlib
import io
import json
import os
import signal
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import anyio
from test_stdio import Fixture

from releasekit import __version__, distribution
from releasekit_mcp import process

SOURCE = Path(__file__).resolve().parents[2] / "src"


def alive(pid):
    if os.name == "nt":
        import pywintypes
        import win32api
        import win32process

        try:
            handle = win32api.OpenProcess(0x1000, False, pid)
        except pywintypes.error:
            return False
        try:
            return win32process.GetExitCodeProcess(handle) == 259
        finally:
            handle.Close()
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def replace_cli(fixture, source):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("__main__.py", distribution.ENTRYPOINT)
        archive.writestr("releasekit/__init__.py", f'__version__ = "{__version__}"\n')
        archive.writestr("releasekit/cli.py", source)
    fixture.projection.write_bytes(stream.getvalue())
    fixture.digest = hashlib.sha256(stream.getvalue()).hexdigest()


class LifetimeTests(Fixture):
    def test_timeout_and_cancellation_stop_deeply_nested_releasekit_commands(self):
        for cancel in (False, True):
            with self.subTest(cancel=cancel):
                ready = self.root / "nested-ready.json"
                trigger = self.root / "nested-trigger"
                late = self.root / "nested-late"
                child = (
                    "import os,json,time\nfrom pathlib import Path\n"
                    f"Path({str(ready)!r}).write_text(json.dumps(os.getpid()))\n"
                    "deadline=time.monotonic()+20\n"
                    f"while not Path({str(trigger)!r}).exists() and time.monotonic()<deadline:\n"
                    " time.sleep(0.01)\n"
                    f"if Path({str(trigger)!r}).exists(): Path({str(late)!r}).write_text('survived')\n"
                )
                script = self.root / "nested.py"
                nested = child
                for _ in range(3):
                    nested = (
                        "import sys\n"
                        f"sys.path.insert(0,{str(SOURCE)!r})\n"
                        "from releasekit import processes\n"
                        f"processes.run([sys.executable,'-c',{nested!r}], timeout=25)\n"
                    )
                script.write_text(nested)
                digest = hashlib.sha256(script.read_bytes()).hexdigest()

                async def scenario(
                    cancel=cancel,
                    script=script,
                    digest=digest,
                    ready=ready,
                    trigger=trigger,
                    late=late,
                ):
                    try:
                        if cancel:
                            with anyio.move_on_after(2) as scope:
                                await process.execute(
                                    script, digest, [], self.root, dict(os.environ), 30
                                )
                            self.assertTrue(scope.cancel_called)
                        else:
                            with self.assertRaises(TimeoutError):
                                await process.execute(
                                    script, digest, [], self.root, dict(os.environ), 2
                                )
                        self.assertTrue(ready.is_file(), "the nested command must have started")
                        trigger.touch()
                        await anyio.sleep(0.3)
                        self.assertFalse(late.exists(), "nested command survived MCP cleanup")
                        self.assertTrue(alive(os.getpid()))
                    finally:
                        # A failing regression still reaps only its own recorded fixture.
                        if ready.is_file():
                            pid = json.loads(ready.read_text())
                            if alive(pid):
                                try:
                                    os.kill(pid, signal.SIGTERM)
                                except ProcessLookupError:
                                    pass
                        for path in (ready, trigger, late):
                            path.unlink(missing_ok=True)

                self.run_async(scenario)

    @unittest.skipIf(os.name == "nt", "POSIX abnormal relay outcome")
    def test_failed_nested_cleanup_is_reported_even_after_the_relay_exits(self):
        ready = self.root / "unknown-worker-pid"
        child = (
            "import os,time\nfrom pathlib import Path\n"
            f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(20)\n"
        )
        script = self.root / "unknown.py"
        script.write_text(
            "import subprocess,sys\n"
            f"sys.path.insert(0,{str(SOURCE)!r})\n"
            "from releasekit import processes\n"
            "def denied(*args): raise PermissionError('controlled cleanup denial')\n"
            "processes._group=denied\n"
            f"processes.run([sys.executable,'-c',{child!r}],timeout=25,"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        )
        digest = hashlib.sha256(script.read_bytes()).hexdigest()

        async def scenario():
            try:
                with self.assertRaises(process.CleanupError):
                    await process.execute(script, digest, [], self.root, dict(os.environ), 2)
                self.assertTrue(ready.is_file())
            finally:
                if ready.is_file():
                    try:
                        os.killpg(int(ready.read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass

        self.run_async(scenario)

    @unittest.skipIf(os.name == "nt", "POSIX nested lifetime")
    def test_inner_timeout_is_unconfirmed_before_the_outer_deadline(self):
        ready = self.root / "early-worker-pid"
        child = (
            "import os,time\nfrom pathlib import Path\n"
            f"Path({str(ready)!r}).write_text(str(os.getpid()))\n"
            "time.sleep(20)\n"
        )
        script = self.root / "early-unknown.py"
        script.write_text(
            "import subprocess,sys\n"
            f"sys.path.insert(0,{str(SOURCE)!r})\n"
            "from releasekit import processes\n"
            "def denied(*args): raise PermissionError('controlled cleanup denial')\n"
            "processes._group=denied\n"
            f"processes.run([sys.executable,'-c',{child!r}],timeout=2,env={{}},"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
        )
        digest = hashlib.sha256(script.read_bytes()).hexdigest()

        async def scenario():
            try:
                with (
                    patch.object(process, "_GRACE", 0.3),
                    self.assertRaises(process.CleanupError),
                ):
                    await process.execute(script, digest, [], self.root, dict(os.environ), 8)
                self.assertTrue(ready.is_file())
            finally:
                if ready.is_file():
                    try:
                        os.killpg(int(ready.read_text()), signal.SIGTERM)
                    except ProcessLookupError:
                        pass

        self.run_async(scenario)

    @unittest.skipIf(os.name == "nt", "POSIX forced relay termination")
    def test_forced_termination_reports_unconfirmed_cleanup(self):
        script = self.root / "ignoring-term.py"
        script.write_text(
            "import signal,time\nfrom pathlib import Path\n"
            "signal.signal(signal.SIGTERM,signal.SIG_IGN)\n"
            "Path('ignoring-ready').touch()\n"
            "time.sleep(20)\n"
        )
        digest = hashlib.sha256(script.read_bytes()).hexdigest()

        async def scenario():
            with (
                patch.object(process, "_GRACE", 0.2),
                self.assertRaisesRegex(process.CleanupError, "forced or abnormal"),
            ):
                await process.execute(script, digest, [], self.root, dict(os.environ), 2)
            self.assertTrue((self.root / "ignoring-ready").is_file())

        self.run_async(scenario)

    def test_stdio_request_cancellation_stops_cli_and_keeps_server_usable(self):
        replace_cli(
            self,
            """
import json,sys,pathlib,subprocess,os,time
from . import __version__
def main():
    if 'notes' in sys.argv:
        child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'])
        pathlib.Path('pids.json').write_text(json.dumps([os.getpid(),child.pid]))
        time.sleep(120)
    print(json.dumps({'schema_version':1,'tool_version':__version__,'command':['version'],
                     'root':None,'status':'ok','exit_code':0,'data':{},'errors':[],
                     'warnings':[],'next_action':None}))
    return 0
""",
        )

        async def scenario():
            async with self.client(mode="legacy") as client:
                with anyio.move_on_after(0.7) as scope:
                    await client.call_tool("relkit_notes", {"request": {"version": "1.0.0"}})
                self.assertTrue(scope.cancel_called)
                await self.assert_stopped()
                result = await client.call_tool("relkit_version", {"request": {}})
                self.assertFalse(result.is_error, result)

        self.run_async(scenario)

    def sleeper(self, *, finish=False):
        script = self.root / "sleeper.py"
        script.write_text(
            "import subprocess,sys,time,json,os,pathlib\n"
            "child=subprocess.Popen([sys.executable,'-c','import time;time.sleep(120)'],"
            "stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)\n"
            "pathlib.Path('pids.json').write_text(json.dumps([os.getpid(),child.pid]))\n"
            + ("print('done')\n" if finish else "time.sleep(120)\n")
        )
        return script, hashlib.sha256(script.read_bytes()).hexdigest()

    async def assert_stopped(self):
        pids = json.loads((self.root / "pids.json").read_text())
        with anyio.fail_after(5):
            while any(alive(pid) for pid in pids):
                await anyio.sleep(0.05)

    def test_timeout_kills_only_owned_tree(self):
        path, digest = self.sleeper()

        async def scenario():
            with self.assertRaises(TimeoutError):
                await process.execute(path, digest, [], self.root, dict(os.environ), 0.7)
            await self.assert_stopped()
            self.assertTrue(alive(os.getpid()))

        self.run_async(scenario)

    @unittest.skipUnless(os.name == "nt", "Windows descendant teardown regression")
    def test_timeout_releases_descendant_cwd_before_returning(self):
        path, digest = self.sleeper()

        async def scenario():
            for index in range(3):
                cwd = self.root / f"child cwd {index}"
                cwd.mkdir()
                with self.assertRaises(TimeoutError):
                    await process.execute(path, digest, [], cwd, dict(os.environ), 0.7)
                marker = cwd / "pids.json"
                self.assertTrue(marker.is_file(), "the descendant must have started")
                marker.unlink()
                cwd.rmdir()

        self.run_async(scenario)

    def test_cancellation_kills_owned_tree(self):
        path, digest = self.sleeper()

        async def scenario():
            with anyio.move_on_after(0.7) as scope:
                await process.execute(path, digest, [], self.root, dict(os.environ), 30)
            self.assertTrue(scope.cancel_called)
            await self.assert_stopped()

        self.run_async(scenario)

    def test_surviving_child_is_stopped_after_success(self):
        path, digest = self.sleeper(finish=True)

        async def scenario():
            code, output, _, _ = await process.execute(
                path, digest, [], self.root, dict(os.environ), 5
            )
            self.assertEqual(0, code)
            self.assertIn(b"done", output)
            await self.assert_stopped()

        self.run_async(scenario)

    def test_output_limit_and_stderr_tail(self):
        script = self.root / "output.py"
        script.write_text("import sys\nprint('x'*200)\nprint('e'*40000,file=sys.stderr)\n")
        digest = hashlib.sha256(script.read_bytes()).hexdigest()

        async def scenario():
            with self.assertRaisesRegex(ValueError, "size limit"):
                await process.execute(script, digest, [], self.root, dict(os.environ), 5, limit=100)
            _, output, errors, truncated = await process.execute(
                script, digest, [], self.root, dict(os.environ), 5
            )
            self.assertLessEqual(len(errors), 32768)
            self.assertTrue(truncated)
            self.assertEqual(202 if os.name == "nt" else 201, len(output))

        self.run_async(scenario)

    @unittest.skipUnless(os.name == "nt", "Windows Job Object barrier")
    def test_assignment_failure_never_starts_project_code(self):
        from unittest.mock import patch

        path, digest = self.sleeper()

        async def scenario():
            with (
                patch("releasekit._winjob.Job.assign", side_effect=OSError("job refused")),
                self.assertRaisesRegex(OSError, "job refused"),
            ):
                await process.execute(path, digest, [], self.root, dict(os.environ), 5)
            self.assertFalse((self.root / "pids.json").exists())

        self.run_async(scenario)


RELEASE_CLI = """
import json,sys,pathlib
from . import __version__
def main():
    argv=sys.argv[1:]
    action=argv[argv.index('release')+1]
    root=argv[argv.index('--root')+1]
    plan={'plan_sha256':'a'*64,'root':root,'sha':'b'*40,'tag':'v1.0.0',
          'repository':'example/consumer','refspecs':['refs/tags/v1.0.0:refs/tags/v1.0.0']}
    if action in ('run','resume'):
        assert '--publish' in argv
        assert '--plan-hash='+'a'*64 in argv
        pathlib.Path(root,'.git','executed.json').write_text(json.dumps(argv))
    data={'plan':plan} if action=='plan' else {'release':{'plan':plan,'publication':'published','verification':'passed'}}
    print(json.dumps({'schema_version':1,'tool_version':__version__,'command':['release',action],
                     'root':root,'status':'ok','exit_code':0,'data':data,'errors':[],
                     'warnings':[],'next_action':None}))
    return 0
"""


class ReleaseRoutingTests(Fixture):
    def test_all_release_actions_and_exact_publish_resume_arguments(self):
        replace_cli(self, RELEASE_CLI)

        async def scenario():
            async with self.client() as client:
                for action in ("plan", "status", "run", "resume"):
                    request = {"action": action, "version": "v1.0.0"}
                    if action in ("run", "resume"):
                        request.update(plan_hash="a" * 64, no_download=True)
                    if action == "resume":
                        request["accept_ci_attempt"] = 2
                    result = await client.call_tool("relkit_release", {"request": request})
                    self.assertFalse(result.is_error, result)
                    if action in ("run", "resume"):
                        argv = json.loads((self.root / ".git/executed.json").read_text())
                        self.assertIn("--no-download", argv)
                        self.assertIn("--publish", argv)
                        self.assertNotIn("--force", argv)
                        self.assertIn(
                            "--accept-ci-attempt=" + ("2" if action == "resume" else "0"), argv
                        )
                    else:
                        self.assertFalse((self.root / ".git/executed.json").exists())
            self.assertEqual(2, len(self.prompts))
            self.assertIn("example/consumer", self.prompts[0])
            self.assertIn("refs/tags/v1.0.0", self.prompts[0])

        self.run_async(scenario)

    def test_release_does_not_publish_without_reviewed_hash(self):
        replace_cli(self, RELEASE_CLI)

        async def scenario():
            async with self.client() as client:
                result = await client.call_tool(
                    "relkit_release", {"request": {"action": "run", "version": "v1.0.0"}}
                )
                self.assertTrue(result.is_error)
            self.assertEqual([], self.prompts)
            self.assertFalse((self.root / ".git/executed.json").exists())

        self.run_async(scenario)
