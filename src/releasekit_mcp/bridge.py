"""Translate closed operations to the bound project's verified structured CLI."""

import hashlib
import json
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import anyio

from releasekit import __version__, distribution, storage
from releasekit import canonical as canonical_json

from . import models, process

# The adapter and the project CLI fingerprint the same reviewed plans; a divergent
# encoder here would silently break every plan_hash comparison across the boundary.
canonical = canonical_json.dumps

MAX_REVIEW_INPUT_BYTES = 8 * 1024 * 1024


@dataclass
class Prepared:
    argv: list[str]
    command: list[str]
    write: bool = False
    review: dict | None = None
    changes_projection: bool = False


class Bridge:
    def __init__(self, root: Path, sha256: str, *, timeout: float = 7200, bundle=None):
        self.root = storage.checked(root)
        if not self.root.is_dir() or not (self.root / ".git").is_dir():
            raise ValueError("bind an ordinary checkout with its own .git directory")
        if redirected := storage.redirected_git():
            raise ValueError(
                "remove inherited Git location overrides before starting the adapter: "
                + ", ".join(redirected)
            )
        try:
            found = subprocess.run(
                ["git", "rev-parse", "--show-toplevel", "--git-common-dir"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
                timeout=30,
            ).stdout.splitlines()
        except (OSError, subprocess.SubprocessError) as error:
            # A refusing Git (safe.directory, a broken checkout, a timeout) is an
            # answer the client can act on, not an adapter traceback.
            detail = (getattr(error, "stderr", "") or "").strip()
            raise ValueError(f"git could not identify the bound checkout: {detail or error}") from (
                error
            )
        if len(found) != 2 or Path(found[0]).resolve() != self.root:
            raise ValueError("project is not the canonical Git checkout root")
        if (self.root / found[1]).resolve() != self.root / ".git":
            raise ValueError("Git metadata must belong to the bound project")
        self.git_identity = storage.identity(self.root / ".git")
        self.root_identity = storage.identity(self.root)
        self.projection = storage.inside(self.root, self.root / ".github/relkit.pyz")
        if self.projection.stat().st_size > distribution.MAX_ARCHIVE_BYTES:
            raise ValueError("projection exceeds the distribution limit")
        self.artifact = distribution.inspect(self.projection.read_bytes())
        if not re.fullmatch(r"[0-9a-fA-F]{64}", sha256) or self.artifact.sha256 != sha256.lower():
            raise ValueError("projection SHA-256 differs from the operator's startup pin")
        if bundle is None and distribution.version_tuple(self.artifact.version) < (0, 9, 0):
            raise ValueError("full MCP requires a project projection of release-kit 0.9.0 or newer")
        # Only the plugin's operator-installed, inventory-verified distribution may
        # update an older projection without executing that project's code.
        self.bundle = bundle
        self.executor = bundle.path if bundle else self.projection
        self.executor_artifact = bundle.artifact if bundle else self.artifact
        if bundle:
            bundle.check()
        self.timeout = timeout
        self.lock = anyio.Lock()

    def check(self):
        if self.bundle:
            self.bundle.check()
        if (
            storage.identity(self.root) != self.root_identity
            or storage.identity(self.root / ".git") != self.git_identity
            or storage.digest(self.projection) != self.artifact.sha256
        ):
            raise ValueError(
                "bound checkout/projection changed; restart with a reviewed SHA-256 pin"
            )

    def path(self, relative: str) -> Path:
        # Reject Windows aliases/ADS even on a POSIX server.
        pieces = relative.replace("\\", "/").split("/")
        if (
            not relative
            or Path(relative).is_absolute()
            or any(part in ("", ".", "..") or part.endswith((".", " ")) for part in pieces)
            or any(char in relative for char in ':\x00\r\n*?"<>|')
            or any(
                re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
                for part in pieces
            )
        ):
            raise ValueError(
                "paths must be plain project-relative paths without traversal or aliases"
            )
        path = storage.inside(self.root, self.root.joinpath(*pieces))
        for parent in path.parents:
            if parent == self.root:
                break
            if (parent / ".git").exists():
                raise ValueError("paths cannot enter a nested independent checkout")
        return path

    def stamp(self, path: Path):
        storage.inside(self.root, path)
        if not path.exists():
            return None
        if not path.is_file() or path.stat().st_size > MAX_REVIEW_INPUT_BYTES:
            raise ValueError("review inputs must be ordinary files of at most 8 MiB")
        return {"sha256": storage.digest(path), "identity": storage.identity(path)}

    async def call(self, argv, command, *, changes_projection=False):
        self.check()
        workspace = storage.Workspace(self.root, "mcp-")
        response = models.Response(
            adapter_version=__version__,
            project=str(self.root),
            projection_sha256=self.artifact.sha256,
        )
        environment = workspace.environment()
        # Do not inherit approval to write outside this project's service storage.
        environment.pop("RELKIT_APPROVED_EXTERNAL_CACHE", None)
        environment.pop("RELKIT_CACHE_DIR", None)
        environment["GIT_PAGER"] = "cat"
        environment["GIT_TERMINAL_PROMPT"] = "0"
        try:
            code, payload, stderr, truncated = await process.execute(
                self.executor,
                self.executor_artifact.sha256,
                ["--json", *argv, *([] if command == ["version"] else ["--root", str(self.root)])],
                self.root,
                environment,
                self.timeout,
            )
            response.diagnostics = stderr
            response.diagnostics_truncated = truncated
            value = json.loads(payload.decode("utf-8"))
            if (
                not isinstance(value, dict)
                or value.get("schema_version") != 1
                or value.get("tool_version") != self.executor_artifact.version
                or value.get("command") != command
                or value.get("root") != (None if command == ["version"] else str(self.root))
                or type(value.get("exit_code")) is not int
                or value.get("exit_code") != code
                or code not in (0, 1, 2, 3)
                or value.get("status") != {0: "ok", 1: "failed", 2: "refused", 3: "pending"}[code]
                or not isinstance(value.get("data"), dict)
                or not isinstance(value.get("errors"), list)
                or not isinstance(value.get("warnings"), list)
                or not all(
                    isinstance(item, dict)
                    and isinstance(item.get("code"), str)
                    and isinstance(item.get("message"), str)
                    for item in [*value.get("errors", []), *value.get("warnings", [])]
                )
                or not (
                    value.get("next_action") is None
                    or isinstance(value.get("next_action"), list)
                    and all(isinstance(arg, str) for arg in value["next_action"])
                )
            ):
                raise ValueError("CLI returned an invalid or mismatched structured result")
            response.result = value
            try:
                self.check()
            except (ValueError, OSError, storage.StorageError):
                response.restart_required = True
                if not changes_projection:
                    response.error = (
                        "projection changed during the operation; inspect before retrying"
                    )
        except TimeoutError:
            response.error = (
                "CLI timed out; owned processes stopped. Remote outcome may be unknown. "
                "Inspect release status/update receipt and lock before retrying; do not auto-retry."
            )
        except (ValueError, OSError, distribution.DistributionError) as error:
            response.error = str(error)
        finally:
            try:
                clean = workspace.cleanup()
            except (OSError, storage.StorageError):
                clean = False
            if not clean:
                response.retained_scratch = str(workspace.path)
                # Cancellation has no result payload, but diagnostics must retain the location.
                import sys

                print(f"relkit-mcp: retained scratch: {workspace.path}", file=sys.stderr)
        return response

    async def prepare(self, request):
        async with self.lock:
            return await self._prepare(request)

    async def _prepare(self, request):
        self.check()
        if isinstance(request, models.Version):
            return Prepared(["--version"], ["version"])
        if isinstance(request, models.Audit):
            argv = ["audit"]
            if request.scope != "worktree":
                argv.append("--" + request.scope)
            for name in ("owner", "strict", "require_overlay", "no_download"):
                if getattr(request, name):
                    argv.append("--" + name.replace("_", "-"))
            return Prepared(argv, ["audit"])
        if isinstance(request, models.Exposure):
            return Prepared(
                [
                    "exposure",
                    *(["--owner"] if request.owner else []),
                    *(["--strict"] if request.strict else []),
                ],
                ["exposure"],
            )
        if isinstance(request, models.Overlay):
            return Prepared(["overlay"], ["overlay"])
        if isinstance(request, models.Notes):
            source = self.path(request.changelog)
            argv = ["notes", request.version, "--changelog=" + str(source)]
            if request.strict:
                argv.append("--strict")
            if request.output is None:
                return Prepared(argv, ["notes"])
            destination = self.path(request.output)
            if (
                ".git" in [part.lower() for part in destination.relative_to(self.root).parts]
                or destination in (source, self.root / "relkit.toml", self.projection)
                or not destination.parent.is_dir()
            ):
                raise ValueError(
                    "notes output cannot replace project policy, Git metadata or the projection"
                )
            inputs = {
                str(p): self.stamp(p) for p in (source, self.root / "relkit.toml", destination)
            }
            result = await self.call(argv, ["notes"])
            data = self.success(result)
            if inputs != {
                str(p): self.stamp(p) for p in (source, self.root / "relkit.toml", destination)
            }:
                raise ValueError("notes inputs changed during preview")
            return Prepared(
                [*argv, "--output=" + str(destination)],
                ["notes"],
                True,
                {
                    "inputs": inputs,
                    "notes": data["notes"],
                    "output": str(destination),
                    "after_sha256": hashlib.sha256(data["notes"].encode()).hexdigest(),
                },
            )
        if isinstance(request, models.Protect):
            if request.action == "check":
                if request.plan_hash:
                    raise ValueError("check does not accept plan_hash")
                return Prepared(["protect", "check"], ["protect", "check"])
            argv = ["protect", "install"]
            if request.action == "plan":
                return Prepared([*argv, "--dry-run"], argv)
            data = self.success(await self.call([*argv, "--dry-run"], argv))
            plan = data["plan"]
            self.reviewed(request.plan_hash, plan["plan_sha256"])
            return Prepared([*argv, "--plan-hash=" + request.plan_hash], argv, True, plan)
        if isinstance(request, models.Release):
            if request.action in ("next", "prepare"):
                if distribution.version_tuple(self.executor_artifact.version) < (0, 20, 0):
                    raise ValueError(
                        "release next/prepare requires release-kit 0.20.0 or newer; sync the project"
                    )
                if request.plan_hash:
                    raise ValueError("next/prepare does not accept a publication plan hash")
                argv = ["release", request.action]
                if request.action == "next":
                    if request.no_download:
                        raise ValueError("next does not accept no_download")
                    argv += ["--bump", request.bump]
                else:
                    argv += [request.version]
                    if request.ci_run:
                        argv += ["--ci-run", str(request.ci_run)]
                    if request.no_download:
                        argv.append("--no-download")
                return Prepared(argv, argv[:2])
            if request.action == "verify":
                if distribution.version_tuple(self.executor_artifact.version) < (0, 19, 0):
                    raise ValueError(
                        "release verify requires release-kit 0.19.0 or newer; sync the project or upgrade the plugin"
                    )
                if request.no_download:
                    raise ValueError(
                        "verify downloads published assets; no_download is not accepted"
                    )
                argv = ["release", "verify", request.version]
                if request.plan_hash:
                    argv.append("--plan-hash=" + request.plan_hash)
                return Prepared(argv, argv[:2])
            action = "status" if request.action == "resume_plan" else request.action
            argv = ["release", action, request.version]
            command = argv[:2]
            if request.no_download and request.action != "resume_plan":
                argv.append("--no-download")
            if action in ("plan", "status"):
                return Prepared(argv, command)
            probe = ["release", "plan" if request.action == "run" else "status"]
            data = self.success(await self.call([*probe, request.version], probe))
            plan = data["plan"] if request.action == "run" else data["release"]["plan"]
            self.reviewed(request.plan_hash, plan["plan_sha256"])
            return Prepared(
                [
                    *argv,
                    "--publish",
                    "--plan-hash=" + request.plan_hash,
                    "--accept-ci-attempt=" + str(request.accept_ci_attempt),
                ],
                command,
                True,
                {"plan": plan, "accept_ci_attempt": request.accept_ci_attempt},
            )
        if isinstance(request, models.Update):
            argv = ["update"]
            if self.bundle and request.action in ("plan", "apply"):
                if any(
                    (
                        request.artifact,
                        request.sha256,
                        request.repository,
                        request.release,
                    )
                ):
                    raise ValueError("bundled updates cannot select another source")
                if not request.refresh_guard:
                    argv += [
                        "--artifact=" + str(self.bundle.path),
                        "--sha256=" + self.bundle.artifact.sha256,
                    ]
            for name in ("repository", "release", "sha256"):
                value = getattr(request, name)
                if value:
                    if any(char in value for char in "\x00\r\n"):
                        raise ValueError("invalid update source")
                    argv.append("--" + name + "=" + value)
            if request.artifact is not None:
                argv.append("--artifact=" + str(self.path(request.artifact)))
            if request.refresh_guard:
                argv.append("--refresh-guard")
            if request.no_download:
                argv.append("--no-download")
            if request.action in ("rollback_plan", "rollback"):
                argv.append("--rollback")
            if request.action in ("plan", "rollback_plan"):
                return Prepared([*argv, "--dry-run"], ["update"])
            data = self.success(await self.call([*argv, "--dry-run"], ["update"]))
            self.reviewed(request.plan_hash, data["plan_sha256"])
            return Prepared(
                [*argv, "--yes", "--plan-hash=" + request.plan_hash],
                ["update"],
                True,
                data["plan"],
                not request.refresh_guard,
            )
        raise ValueError("unsupported operation")

    @staticmethod
    def success(response):
        if response.error or response.result is None or response.result["exit_code"]:
            raise ValueError("preflight refused: " + canonical(response.model_dump()))
        return response.result["data"]

    @staticmethod
    def reviewed(supplied, actual):
        if not re.fullmatch(r"[0-9a-f]{64}", supplied) or supplied != actual:
            raise ValueError("plan_hash is missing or stale; request and review a fresh plan")

    async def invoke(self, request, prepared, approval):
        async with self.lock:
            if prepared.write:
                if not approval.approve:
                    raise ValueError("operation was not approved; nothing applied")
                fresh = await self._prepare(request)
                if canonical(fresh.__dict__) != canonical(prepared.__dict__):
                    raise ValueError("inputs changed after confirmation; request a fresh plan")
            return await self.call(
                prepared.argv, prepared.command, changes_projection=prepared.changes_projection
            )
