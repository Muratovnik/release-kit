"""Small native Git and GitHub CLI adapters. Only push/tag mutate remote/Git state."""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from urllib.parse import quote

from .. import processes, semver, storage


class ReleaseError(RuntimeError):
    """A release cannot safely advance."""


class Pending(ReleaseError):
    """Remote work has not completed; resume can observe it again."""


class CommandError(ReleaseError):
    def __init__(
        self, program: str, result: subprocess.CompletedProcess, message: str | None = None
    ):
        self.result = result
        super().__init__(message or f"{program} exited {result.returncode}; see the release log")


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
            result = processes.run(
                args,
                cwd=cwd or self.root,
                env=env,
                check=False,
                stdout=handle or subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as error:
            raise Pending(
                f"{args[0]} timed out after {timeout}s; if it changed remote state, "
                "reconcile with `relkit release resume VERSION --publish`"
            ) from error
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

    def api(
        self,
        suffix: str = "",
        *,
        optional: bool = False,
        pages: bool = False,
        method: str = "GET",
        payload: Path | None = None,
    ):
        args = [
            "gh",
            "api",
            "--hostname",
            "github.com",
            "--method",
            method,
            f"repos/{self.repository}{suffix}",
        ]
        if payload is not None:
            args += ["--input", str(payload)]
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
        published = self.api(f"/releases/tags/{quote(tag, safe='')}", optional=True)
        if published is not None:
            return published
        # The tag endpoint only returns published releases. Drafts are visible
        # through the authenticated releases list to callers with push access.
        matching = [item for item in self.releases() if item["tag_name"] == tag]
        if len(matching) > 1:
            raise ReleaseError(
                f"multiple releases or drafts ({len(matching)}) claim the tag {tag} in "
                f"{self.repository}; "
                "delete the extras on its Releases page so exactly one remains, then retry"
            )
        return matching[0] if matching else None

    def release_by_id(self, identifier: int):
        """Read the exact resource this run created.

        The releases list is eventually consistent, so a draft created a moment ago can
        be missing from it; reading the release by its own id is not. Every ownership
        condition still applies to whatever this returns.
        """
        return self.api(f"/releases/{int(identifier)}", optional=True)

    def tag_object(self, tag: str) -> str | None:
        reference = self.api(f"/git/ref/tags/{quote(tag, safe='')}", optional=True)
        return (reference or {}).get("object", {}).get("sha")

    def identity(self):
        return self.api()

    def releases(self):
        return [item for page in self.api("/releases?per_page=100", pages=True) for item in page]

    def preflight(self):
        if not (self.api("/immutable-releases", optional=True) or {}).get("enabled"):
            raise ReleaseError(
                f"enable immutable releases for {self.repository} (Settings > General > "
                "Releases) before publishing; no tag was created. This check needs repository "
                "administration read access"
            )

    def create_draft(self, tag, sha, title, notes: Path, *, tag_oid: str) -> dict:
        """Create the draft and keep the server's answer, which names what it created.

        `gh release create` reports nothing a caller can address, so this used to be
        followed by a search of the releases list for the draft that had just been
        written — a list that had not caught up, which cost two publications a full
        run. Creating through the API returns the release itself, id included.

        The API creates a missing tag from `target_commitish`, which `--verify-tag`
        used to prevent. The tag is therefore checked here against the object this run
        pushed: existence alone would not prove it is the same tag.
        """
        observed = self.tag_object(tag)
        if observed != tag_oid:
            raise ReleaseError(
                f"{tag} resolves to {observed or 'nothing'}, not to {tag_oid} pushed by this "
                "run; refusing to create a release that would define the tag instead"
            )
        request = notes.with_name(notes.name + ".request.json")
        request.write_text(
            json.dumps(
                {
                    "tag_name": tag,
                    "target_commitish": sha,
                    "name": title,
                    "body": notes.read_text(encoding="utf-8"),
                    "draft": True,
                    "prerelease": semver.parse(tag.removeprefix("v")).is_prerelease,
                }
            ),
            encoding="utf-8",
        )
        try:
            return self.api("/releases", method="POST", payload=request)
        finally:
            request.unlink(missing_ok=True)

    def upload(self, tag, path):
        # Never clobber: a resumed upload must reconcile the existing asset first.
        self.runner.call(
            ["gh", "release", "upload", tag, str(path), "--repo", self.repository], timeout=600
        )

    def publish(self, tag):
        self.runner.call(
            [
                "gh",
                "release",
                "edit",
                tag,
                "--repo",
                self.repository,
                "--draft=false",
                "--title",
                tag,
            ]
        )

    def release_attestation(self, tag: str) -> dict:
        """The statement GitHub signs about an immutable release and its exact assets.

        `gh` performs the Sigstore verification and this returns only what it verified,
        so the coordinator compares a signed claim against its own plan instead of an
        unsigned REST response. Needs the documented `gh` floor and an immutable
        release; both are already required to coordinate a release at all.
        """
        try:
            payload = json.loads(
                self.runner.call(
                    ["gh", "release", "verify", tag, "--repo", self.repository, "--format", "json"]
                )
            )
        except CommandError as error:
            detail = (error.result.stderr or error.result.stdout or "").strip()[-500:]
            raise ReleaseError(
                f"GitHub's signed release attestation for {tag} could not be verified: {detail}"
            ) from error
        except ValueError as error:
            raise ReleaseError("gh returned invalid release attestation JSON") from error
        result = payload.get("verificationResult") if isinstance(payload, dict) else None
        statement = result.get("statement") if isinstance(result, dict) else None
        if not isinstance(statement, dict):
            raise ReleaseError("verified release attestation carries no in-toto statement")
        return statement

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
        self,
        tag: str,
        sha: str,
        workflow: str,
        paths: list[Path],
        *,
        ci: dict,
        repository_id: int,
        provenance: bool = True,
    ) -> None:
        """Verify the immutable release, its asset membership and, when declared, the
        build provenance of every asset.

        Without provenance the published set and its digests still come from the
        release attestation GitHub signed, checked by the two native commands below.
        What is not proven is which workflow run produced those bytes: the CI run is
        then only the one this coordinator selected and observed. That is a weaker
        claim, so it is never the default and never inferred.
        """
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
            if not provenance:
                continue
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
    if redirected := storage.redirected_git():
        raise ReleaseError(
            "unset Git directory/index/object overrides before coordinating a release: "
            + ", ".join(redirected)
        )
    toplevel = Path(runner.git("rev-parse", "--show-toplevel")).resolve()
    if toplevel != root:
        raise ReleaseError(f"--root {root} is not the repository root; pass --root {toplevel}")
    if not (root / ".git").is_dir():
        raise ReleaseError(
            f"{root / '.git'} is not a project-local Git directory (a linked worktree has a "
            "file there); run release coordination from the main checkout"
        )
    for option in ("--absolute-git-dir", "--git-common-dir"):
        found = (root / runner.git("rev-parse", option)).resolve()
        if found != storage.checked(root / ".git"):
            raise ReleaseError(
                f"Git metadata for {root} resolves to {found}, outside {root / '.git'}; "
                "release coordination needs project-local Git metadata"
            )
    if runner.git("rev-parse", "--is-shallow-repository") != "false":
        raise ReleaseError(
            f"complete Git history is required but {root} is a shallow clone; "
            "run `git fetch --unshallow` there, then plan again"
        )
    if runner.git("for-each-ref", "--format=%(refname)", "refs/replace"):
        raise ReleaseError(
            f"refs/replace has entries in {root}, which make release ancestry ambiguous; "
            "remove them with `git replace -d`"
        )
    if (root / ".git/info/grafts").exists():
        raise ReleaseError(
            f"{root / '.git/info/grafts'} makes release ancestry ambiguous; remove the file"
        )


def _changes(runner: Runner) -> list[tuple[str, str]]:
    """Status code and exact path of every change, read NUL-separated so no path is quoted."""
    records = runner.call(
        [
            "git",
            "status",
            "--porcelain=v1",
            "-z",
            "--untracked-files=all",
            "--ignore-submodules=none",
        ]
    ).split("\0")
    changes: list[tuple[str, str]] = []
    index = 0
    while index < len(records):
        record = records[index]
        index += 1
        if not record:
            continue
        code, path = record[:2], record[3:]
        changes.append((code, path))
        if code[0] in "RC" or code[1] in "RC":
            index += 1  # a rename or copy carries its source as the next record
    return changes


def clean(runner: Runner, sha: str | None = None) -> None:
    dirty = _changes(runner)
    if dirty:
        shown = "\n".join(f"  {code} {path}" for code, path in dirty[:5])
        if len(dirty) > 5:
            shown += f"\n  ... and {len(dirty) - 5} more"
        untracked = [path for code, path in dirty if code == "??"]
        fix = ""
        if untracked:
            # Owner decision: an untracked runtime file stays dirt for a release, because
            # only the adopting repository can say it is not part of the source. Naming
            # the paths and the one fix that applies is the whole remedy.
            listed = ", ".join(untracked[:5]) + (
                f" and {len(untracked) - 5} more" if len(untracked) > 5 else ""
            )
            fix = (
                f"\nuntracked: {listed}; if it is runtime output, add it to .gitignore "
                "(one line per path) and commit .gitignore, otherwise delete it"
            )
        raise ReleaseError(
            f"release preparation requires a clean repository ({len(dirty)} change(s) in "
            f"{runner.root}); commit them or remove what is not needed. Nothing was committed "
            f"or stashed:\n{shown}{fix}"
        )
    if sha and (head := runner.git("rev-parse", "HEAD")) != sha:
        raise ReleaseError(
            f"HEAD is {head[:12]} but the release plan pinned {sha[:12]}; check out "
            f"{sha[:12]} or plan again for the new HEAD"
        )
    # Git status intentionally hides these working-tree/index differences.
    hidden = [
        line[2:]
        for line in runner.git("ls-files", "-v").splitlines()
        if line[:1] in {"S"} or line[:1].islower()
    ]
    if hidden:
        listed = ", ".join(hidden[:5]) + (", ..." if len(hidden) > 5 else "")
        raise ReleaseError(
            f"{len(hidden)} file(s) carry sparse/skip-worktree/assume-unchanged flags ({listed}); "
            "clear them with `git update-index --no-skip-worktree --no-assume-unchanged -- PATH` "
            "before preparing a release"
        )


def remote_identity(runner: Runner, remote: str, expected: str) -> None:
    for option in ([], ["--push"]):
        urls = runner.git("remote", "get-url", *option, "--all", remote).splitlines()
        kind = "push" if option else "fetch"
        if len(urls) != 1:
            raise ReleaseError(
                f"release requires exactly one {kind} URL for remote {remote}, found {len(urls)}"
            )
        match = re.fullmatch(
            r"(?:https://github\.com/|git@github\.com:|ssh://git@github\.com/)([^/]+/[^/]+?)(?:\.git)?",
            urls[0],
        )
        if not match or match[1].casefold() != expected.casefold():
            raise ReleaseError(
                f"{remote} {kind} URL {urls[0]} does not match release.repository {expected} on "
                f"github.com; fix it with `git remote set-url{' --push' if option else ''} "
                f"{remote} https://github.com/{expected}.git`"
            )
    # push defaults can silently add refs or change the destination despite an explicit refspec.
    for key in (f"remote.{remote}.mirror", "push.followTags"):
        try:
            if runner.git("config", "--bool", "--get", key) == "true":
                raise ReleaseError(
                    f"{key}=true conflicts with exact release ref pushes; unset it with "
                    f"`git config --unset {key}` (add --global if it is set there)"
                )
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


def tag_commits(runner: Runner) -> dict[str, str]:
    """Every tag's commit, peeled in one process instead of one per tag."""
    answer: dict[str, str] = {}
    for line in runner.call(
        ["git", "for-each-ref", "--format=%(refname)\t%(objectname)\t%(*objectname)", "refs/tags"]
    ).splitlines():
        ref, object_id, peeled = line.split("\t")
        answer[ref] = peeled or object_id
    return answer


def merged_tags(runner: Runner, commit: str) -> set[str]:
    """Tags reachable from the commit; Git peels annotated tags for this filter."""
    return set(
        runner.git(
            "for-each-ref", "--format=%(refname)", "--merged", commit, "refs/tags"
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
