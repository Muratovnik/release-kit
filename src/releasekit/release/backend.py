"""Small native Git and GitHub CLI adapters. Only push/tag mutate remote/Git state."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from .. import storage


class ReleaseError(RuntimeError):
    """A release cannot safely advance."""


class Pending(ReleaseError):
    """Remote work has not completed; resume can observe it again."""


class CommandError(ReleaseError):
    def __init__(self, program: str, result: subprocess.CompletedProcess):
        self.result = result
        super().__init__(f"{program} exited {result.returncode}; see the release log")


class Runner:
    def __init__(self, root: Path):
        self.root = root
        self.log: Path | None = None
        self.temporary: Path | None = None

    def call(
        self,
        args: list[str],
        *,
        cwd: Path | None = None,
        timeout: int = 120,
        binary: bool = False,
        output: Path | None = None,
    ) -> str | bytes:
        env = {
            **os.environ,
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GH_HOST": "github.com",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
        }
        if self.temporary:
            for key in (
                "TMP",
                "TEMP",
                "TMPDIR",
                "XDG_CACHE_HOME",
                "XDG_STATE_HOME",
                "XDG_DATA_HOME",
            ):
                env[key] = str(self.temporary)
            env["PYTHONDONTWRITEBYTECODE"] = "1"
        handle = storage.checked(output).open("xb") if output else None
        try:
            result = subprocess.run(
                args,
                cwd=cwd or self.root,
                env=env,
                check=False,
                stdout=handle or subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise Pending(f"{args[0]} timed out; reconcile remote state with resume") from error
        except OSError as error:
            raise ReleaseError(f"cannot execute {args[0]}: {error}") from error
        finally:
            if handle:
                handle.close()
        if self.log:
            # Never record argv (which may include secrets) or the environment.
            with storage.checked(self.log).open("ab") as stream:
                stream.write(f"\n[{args[0]} exit={result.returncode}]\n".encode())
                for value in (result.stdout, result.stderr):
                    if value and not binary:
                        stream.write(value)
        if result.returncode:
            raise CommandError(args[0], result)
        value = result.stdout or b""
        return value if binary else value.decode("utf-8", errors="strict")

    def git(self, *args: str) -> str:
        return self.call(["git", *args]).strip()


class GitHub:
    def __init__(self, runner: Runner, repository: str):
        self.runner, self.repository = runner, repository

    def api(self, suffix: str = "", *, optional: bool = False, pages: bool = False):
        args = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            f"repos/{self.repository}{suffix}",
        ]
        if pages:
            args += ["--paginate", "--slurp"]
        try:
            return json.loads(self.runner.call(args))
        except CommandError as error:
            try:
                status = json.loads(error.result.stdout).get("status")
            except (ValueError, AttributeError):
                status = None
            if optional and str(status) == "404":
                return None
            raise

    def release(self, tag: str):
        return self.api(f"/releases/tags/{quote(tag, safe='')}", optional=True)

    def assets(self, release_id: int) -> list[dict]:
        return [
            asset
            for page in self.api(f"/releases/{release_id}/assets?per_page=100", pages=True)
            for asset in page
        ]

    def runs(self, workflow_id: int, sha: str) -> list[dict]:
        pages = self.api(
            f"/actions/workflows/{workflow_id}/runs?event=push&head_sha={sha}&per_page=100",
            pages=True,
        )
        if any(page.get("total_count", 0) >= 1000 for page in pages):
            raise ReleaseError(
                "CI query reached GitHub's filtered result limit; selection is ambiguous"
            )
        return [run for page in pages for run in page["workflow_runs"]]

    def jobs(self, run_id: int, attempt: int) -> list[dict]:
        return [
            job
            for page in self.api(
                f"/actions/runs/{run_id}/attempts/{attempt}/jobs?per_page=100", pages=True
            )
            for job in page["jobs"]
        ]

    def download(self, asset: dict, destination: Path) -> None:
        self.runner.call(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                f"repos/{self.repository}/releases/assets/{asset['id']}",
                "-H",
                "Accept: application/octet-stream",
            ],
            binary=True,
            output=destination,
            timeout=600,
        )

    def signatures(
        self, tag: str, sha: str, workflow: str, paths: list[Path], *, ci: dict, repository_id: int
    ) -> None:
        repo = f"github.com/{self.repository}"
        self.runner.call(["gh", "release", "verify", tag, "--repo", repo, "--format", "json"])
        for path in paths:
            self.runner.call(
                [
                    "gh",
                    "release",
                    "verify-asset",
                    tag,
                    str(path),
                    "--repo",
                    repo,
                    "--format",
                    "json",
                ]
            )
            verified = self.runner.call(
                [
                    "gh",
                    "attestation",
                    "verify",
                    str(path),
                    "--hostname",
                    "github.com",
                    "--repo",
                    self.repository,
                    "--source-ref",
                    f"refs/tags/{tag}",
                    "--source-digest",
                    sha,
                    "--signer-workflow",
                    f"{self.repository}/{workflow}",
                    "--signer-digest",
                    sha,
                    "--format",
                    "json",
                ],
                timeout=600,
            )
            # Certificate extensions are issuer-supplied. The workflow-controlled
            # predicate is not evidence of which CI run signed these bytes.
            invocation = f"https://github.com/{self.repository}/actions/runs/{ci['id']}/attempts/{ci['attempt']}"
            results = json.loads(verified)
            certificates = [
                result["verificationResult"]["signature"]["certificate"] for result in results
            ]
            if not any(
                certificate.get("runInvocationURI") == invocation
                and certificate.get("sourceRepositoryIdentifier") == str(repository_id)
                and certificate.get("buildTrigger") == "push"
                for certificate in certificates
            ):
                raise ReleaseError(
                    "verified artifact signature does not belong to the observed repository/CI attempt"
                )


def repository(runner: Runner) -> None:
    root = storage.checked(runner.root)
    if any(
        os.environ.get(name)
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_COMMON_DIR",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
        )
    ):
        raise ReleaseError(
            "unset Git directory/index/object overrides before coordinating a release"
        )
    if Path(runner.git("rev-parse", "--show-toplevel")).resolve() != root:
        raise ReleaseError("--root must name the repository root")
    if not (root / ".git").is_dir():
        raise ReleaseError(
            "release coordination currently requires project-local .git, not a linked worktree"
        )
    for option in ("--absolute-git-dir", "--git-common-dir"):
        if (root / runner.git("rev-parse", option)).resolve() != storage.checked(root / ".git"):
            raise ReleaseError("external Git metadata is not supported by release coordination")
    if runner.git("rev-parse", "--is-shallow-repository") != "false":
        raise ReleaseError("complete Git history is required; fetch it explicitly before planning")
    if (
        runner.git("for-each-ref", "--format=%(refname)", "refs/replace")
        or (root / ".git/info/grafts").exists()
    ):
        raise ReleaseError("replace refs and grafts make release ancestry ambiguous")


def clean(runner: Runner, sha: str | None = None) -> None:
    if runner.git("status", "--porcelain=v1", "--untracked-files=all", "--ignore-submodules=none"):
        raise ReleaseError(
            "release preparation requires a clean repository; no files were committed or stashed"
        )
    if sha and runner.git("rev-parse", "HEAD") != sha:
        raise ReleaseError("HEAD changed since the release plan")
    # Git status intentionally hides these working-tree/index differences.
    if any(
        line[:1] in {"S"} or line[:1].islower()
        for line in runner.git("ls-files", "-v").splitlines()
    ):
        raise ReleaseError(
            "clear sparse/skip-worktree/assume-unchanged flags before preparing a release"
        )


def remote_identity(runner: Runner, remote: str, expected: str) -> None:
    for option in ([], ["--push"]):
        urls = runner.git("remote", "get-url", *option, "--all", remote).splitlines()
        if len(urls) != 1:
            raise ReleaseError("release requires exactly one fetch URL and one push URL")
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?",
            urls[0],
        )
        if not match or match[1].casefold() != expected.casefold():
            raise ReleaseError(
                "fetch/push remote identity does not match release.repository on github.com"
            )
    # push defaults can silently add refs or change the destination despite an explicit refspec.
    for key in (f"remote.{remote}.mirror", "push.followTags"):
        try:
            if runner.git("config", "--bool", "--get", key) == "true":
                raise ReleaseError(f"{key}=true conflicts with exact release ref pushes")
        except CommandError as error:
            if error.result.returncode != 1:
                raise


def remote_refs(runner: Runner, remote: str) -> dict[str, str]:
    result = {}
    for line in runner.git("ls-remote", "--tags", "--heads", remote).splitlines():
        oid, ref = line.split("\t")
        if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", oid) or ref in result:
            raise ReleaseError("invalid or duplicate remote Git reference")
        result[ref] = oid
    return result


def local_tags(runner: Runner) -> dict[str, str]:
    return dict(
        line.split("\t", 1)
        for line in runner.git(
            "for-each-ref", "--format=%(refname)\t%(objectname)", "refs/tags"
        ).splitlines()
    )


def ancestor(runner: Runner, older: str, newer: str) -> bool:
    try:
        runner.git("merge-base", "--is-ancestor", older, newer)
        return True
    except CommandError as error:
        if error.result.returncode == 1:
            return False
        raise
