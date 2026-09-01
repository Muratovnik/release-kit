"""Explicit per-repository updates with integrity checks and a recoverable transaction."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
from pathlib import Path
from urllib.parse import quote

from . import config, distribution, protection, storage

PROJECTION = protection.PROJECTION_PATH
RECEIPT = "relkit-update.json"


class UpdateError(Exception):
    """An update cannot be completed safely; do not weaken policy to proceed."""


def _run(
    command: list[str], root: Path, *, timeout: int = 180, environment: dict[str, str] | None = None
) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env={
                **(environment if environment is not None else os.environ),
                "GIT_OPTIONAL_LOCKS": "0",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise UpdateError(f"{command[0]} could not complete: {error}") from error
    if result.returncode:
        detail = (result.stderr or result.stdout).strip()[-2000:]
        raise UpdateError(f"{command[0]} failed (exit {result.returncode}): {detail}")
    return result.stdout


def _safe_path(path: Path, root: Path) -> Path:
    if path.resolve() != path.absolute() or not path.resolve().is_relative_to(root):
        raise UpdateError(f"refusing an aliased or escaping update path: {path}")
    storage.checked(path)
    return path


def _repository(root: Path) -> Path:
    if any(
        os.environ.get(name)
        for name in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE")
    ):
        raise UpdateError("unset Git directory/index environment overrides before updating")
    top = Path(_run(["git", "rev-parse", "--show-toplevel"], root).strip()).resolve()
    if top != root:
        raise UpdateError("--root must name the repository root, not a subdirectory")
    git_dir = root / ".git"
    if not git_dir.is_dir():
        raise UpdateError(
            "linked worktrees and external Git directories require an owner-managed update"
        )
    _safe_path(git_dir, root)
    for option in ("--absolute-git-dir", "--git-common-dir"):
        reported = Path(_run(["git", "rev-parse", option], root).strip())
        if (root / reported).resolve() != git_dir:
            raise UpdateError("external Git directories require an owner-managed update")
    return git_dir


def _read_artifact(path: Path) -> tuple[bytes, distribution.Artifact]:
    if path.stat().st_size > distribution.MAX_ARCHIVE_BYTES:
        raise UpdateError("distribution exceeds the archive size limit")
    payload = path.read_bytes()
    return payload, distribution.inspect(payload)


def _guard(root: Path, git_dir: Path, *, refresh: bool = False) -> tuple[Path, bytes | None, int]:
    path = _safe_path(git_dir / "hooks/pre-push", root)
    if path.exists():
        if refresh:
            protection.recorded_digests(root)
            effective = protection.effective_hook_path(root)
            if effective != path and (problem := protection._dispatcher_problem(effective)):
                raise UpdateError(problem)
        elif problem := protection.problem(root):
            raise UpdateError(f"existing guard must be valid before updating: {problem}")
        return path, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
    return path, None, 0o755


def _confirm(yes: bool) -> None:
    print(
        "relkit update: confirmation must include any hook permission required by the project's AGENTS.md"
    )
    if yes:
        return
    if not sys.stdin.isatty():
        raise UpdateError(
            "review --dry-run first; pass --yes only after approving the displayed file/guard changes"
        )
    if input("Apply these changes? [y/N] ").strip().casefold() not in {"y", "yes"}:
        raise UpdateError("cancelled; no project files or guard changed")


def _clean(root: Path) -> None:
    if _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], root).strip():
        raise UpdateError(
            "update requires a clean checkout; commit or resolve changes without auto-stashing"
        )


def _tracked(root: Path) -> None:
    for path in (PROJECTION, config.CONFIG_NAME):
        if not _run(["git", "ls-files", "--error-unmatch", "--", path], root).strip():
            raise UpdateError(f"update requires a tracked {path}")


def _github(
    root: Path, repository: str, release: str, directory: Path
) -> tuple[bytes, distribution.Artifact]:
    repository = distribution.repository_name(repository)
    if release:
        distribution.version_tuple(release.removeprefix("v"))
    endpoint = f"repos/{repository}/releases/" + (
        f"tags/{quote(release, safe='')}" if release else "latest"
    )
    try:
        metadata = json.loads(
            _run(
                [
                    "gh",
                    "api",
                    "--hostname",
                    "github.com",
                    endpoint,
                    "--jq",
                    "{tag_name, draft, prerelease, published_at, assets: [.assets[] | {name, state, size, digest}]}",
                ],
                root,
                timeout=60,
                environment=storage.environment(directory),
            )
        )
    except json.JSONDecodeError as error:
        raise UpdateError("GitHub returned invalid release metadata") from error
    if (
        not isinstance(metadata, dict)
        or metadata.get("draft") is not False
        or metadata.get("prerelease") is not False
        or not metadata.get("published_at")
    ):
        raise UpdateError("update requires a published, non-draft, non-prerelease GitHub release")
    tag = metadata.get("tag_name", "")
    if not isinstance(tag, str):
        raise UpdateError("release tag is missing")
    version = tag.removeprefix("v")
    distribution.version_tuple(version)
    if release and tag != release:
        raise UpdateError("GitHub release tag does not match the requested tag")
    assets = metadata.get("assets", [])
    if not isinstance(assets, list):
        raise UpdateError("invalid release asset list")
    candidates = [a for a in assets if isinstance(a, dict) and a.get("name") == "relkit.pyz"]
    if len(candidates) != 1:
        raise UpdateError(
            "release must have exactly one relkit.pyz asset; publish a versioned build first"
        )
    asset = candidates[0]
    digest = asset.get("digest", "")
    size = asset.get("size")
    if (
        asset.get("state") != "uploaded"
        or type(size) is not int
        or not 0 < size <= distribution.MAX_ARCHIVE_BYTES
    ):
        raise UpdateError("release asset is incomplete or exceeds the size limit")
    if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise UpdateError(
            "release asset lacks a GitHub SHA-256 digest; refusing an unverified download"
        )
    downloaded = directory / "relkit.pyz"
    _run(
        [
            "gh",
            "release",
            "download",
            tag,
            "--repo",
            f"github.com/{repository}",
            "--pattern",
            "relkit.pyz",
            "--output",
            str(downloaded),
        ],
        root,
        environment=storage.environment(directory),
    )
    payload, artifact = _read_artifact(downloaded)
    if len(payload) != size or artifact.sha256 != digest.removeprefix("sha256:"):
        raise UpdateError("downloaded release asset does not match GitHub's size/SHA-256")
    if artifact.version != version or artifact.repository.casefold() != repository.casefold():
        raise UpdateError("release artifact version/repository does not match its GitHub release")
    return payload, artifact


def _atomic(path: Path, payload: bytes, mode: int) -> None:
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            prefix=".relkit-", dir=path.parent, delete=False
        ) as handle:
            temporary = Path(handle.name)
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _save_receipt(path: Path, receipt: dict[str, object]) -> None:
    _atomic(path, (json.dumps(receipt, indent=2) + "\n").encode(), 0o600)


def _load_receipt(path: Path) -> dict[str, object]:
    receipt = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(receipt, dict) or receipt.get("schema") != 1:
        raise UpdateError("invalid update receipt; preserve the backup for manual recovery")
    if receipt.get("state") not in {"pending", "installed", "rolled-back"}:
        raise UpdateError("invalid update receipt state")
    if not isinstance(receipt.get("inputs"), dict):
        raise UpdateError("invalid update receipt inputs")
    return receipt


def _check_inputs(root: Path, inputs: dict[str, str]) -> None:
    for relative, digest in inputs.items():
        path = _safe_path(root / relative, root)
        if protection._sha256(path) != digest:
            raise UpdateError(
                f"guarded input changed during update: {relative}; refusing to re-pin it"
            )


def _restore(root: Path, git_dir: Path, receipt: dict[str, object]) -> None:
    if receipt.get("root") != str(root):
        raise UpdateError("rollback receipt belongs to a different checkout")
    backup_name = receipt.get("backup")
    if not isinstance(backup_name, str) or not re.fullmatch(
        r"relkit-update-[A-Za-z0-9_-]+", backup_name
    ):
        raise UpdateError("invalid rollback backup directory")
    backup = _safe_path(git_dir / backup_name, root)
    projection = _safe_path(root / PROJECTION, root)
    previous, artifact = _read_artifact(_safe_path(backup / "relkit.pyz", root))
    if artifact.sha256 != receipt["old_sha256"]:
        raise UpdateError("rollback backup has changed")
    _check_inputs(root, receipt["inputs"])
    if protection._sha256(projection) not in {receipt["old_sha256"], receipt["new_sha256"]}:
        raise UpdateError("installed artifact has changed since the update; refusing rollback")
    hook = _safe_path(git_dir / "hooks/pre-push", root)
    old_hook = None
    if receipt["guard_sha256"] is not None:
        old_hook = _safe_path(backup / "pre-push", root).read_bytes()
        if protection._sha256(backup / "pre-push") != receipt["guard_sha256"]:
            raise UpdateError("guard backup has changed")
        # Pending transactions may have installed the deterministic refreshed hook
        # before writing the receipt's completed state.
        expected = protection.hook_content(root).encode()
        if not hook.exists() or hook.read_bytes() not in (old_hook, expected):
            raise UpdateError("guard has changed since the update; refusing rollback")
    elif hook.exists():
        raise UpdateError("a guard appeared since the update; refusing rollback")
    if projection.read_bytes() != previous:
        _atomic(projection, previous, int(receipt["projection_mode"]))
    if old_hook is not None:
        _atomic(hook, old_hook, int(receipt["guard_mode"]))


def run(
    root: Path,
    *,
    artifact_path: Path | None = None,
    sha256: str = "",
    repository: str = "",
    release: str = "",
    dry_run: bool = False,
    rollback: bool = False,
    no_download: bool = False,
    yes: bool = False,
    refresh_guard: bool = False,
) -> int:
    """Update one tracked projection, never Git refs/index, policy or external hooks."""
    root = root.resolve()
    lock: Path | None = None
    locked = False
    try:
        git_dir = _repository(root)
        lock = _safe_path(git_dir / "relkit-update.lock", root)
        receipt_path = _safe_path(git_dir / RECEIPT, root)
        # An exclusive owner-repository lock serializes updates and crash recovery.
        try:
            with lock.open("x", encoding="utf-8") as handle:
                locked = True
                handle.write(str(os.getpid()))
        except FileExistsError as error:
            raise UpdateError(
                f"update lock exists: {lock}; check its process before removing a stale lock"
            ) from error
        if rollback:
            if any((artifact_path, sha256, repository, release, dry_run, refresh_guard)):
                raise UpdateError(
                    "--rollback cannot be combined with source selection or --dry-run"
                )
            receipt = _load_receipt(receipt_path)
            print(f"relkit update: restore {receipt['old_version']} from {receipt['backup']}")
            _confirm(yes)
            _restore(root, git_dir, receipt)
            receipt["state"] = "rolled-back"
            _save_receipt(receipt_path, receipt)
            print(
                f"relkit update: restored {receipt['old_version']}; Git index and refs were not changed"
            )
            return 0
        if receipt_path.exists():
            receipt = _load_receipt(receipt_path)
            if receipt.get("state") == "pending":
                raise UpdateError(
                    "an interrupted update needs `relkit update --rollback` before retrying"
                )
        settings = config.load(root)
        _tracked(root)
        projection = _safe_path(root / PROJECTION, root)
        old_payload, old = _read_artifact(projection)
        hook, old_hook, hook_mode = _guard(root, git_dir, refresh=refresh_guard)
        if old_hook is not None and distribution.version_tuple(old.version) < (0, 5, 0):
            raise UpdateError(
                "guard ownership before 0.5.0 requires the documented side-by-side migration"
            )
        inputs = {
            config.CONFIG_NAME: protection._sha256(_safe_path(root / config.CONFIG_NAME, root))
        }
        if settings.exposure.check_secrets:
            relative = settings.exposure.betterleaks_config
            inputs[relative] = protection._sha256(_safe_path(root / relative, root))
        with storage.temporary(root, "download-") as workspace:
            temporary = workspace.path
            if refresh_guard:
                if any((artifact_path, sha256, repository, release)) or old_hook is None:
                    raise UpdateError(
                        "--refresh-guard requires an existing supported guard and no update source"
                    )
                changes = protection.digest_changes(root)
                if not changes:
                    print("relkit update: guard already matches the current inputs")
                    return 0
                for change in changes:
                    print(f"relkit update: {change}")
                payload, candidate = old_payload, old
            elif artifact_path is not None:
                if repository or release or not re.fullmatch(r"[0-9a-fA-F]{64}", sha256):
                    raise UpdateError(
                        "--artifact requires --sha256 and cannot be combined with GitHub selection"
                    )
                payload, candidate = _read_artifact(artifact_path.resolve())
                if candidate.sha256 != sha256.lower():
                    raise UpdateError(
                        "local artifact SHA-256 does not match the explicitly supplied digest"
                    )
            else:
                if sha256:
                    raise UpdateError("--sha256 requires --artifact")
                repository = repository or old.repository
                if not repository:
                    raise UpdateError(
                        "no update repository recorded; supply --repository OWNER/REPO or --artifact with --sha256"
                    )
                payload, candidate = _github(root, repository, release, Path(temporary))
                workspace.remember(Path(temporary) / "relkit.pyz")
            if candidate.sha256 == old.sha256 and not refresh_guard:
                print(f"relkit update: already current ({old.version}, sha256:{old.sha256})")
                return 0
            if not refresh_guard and distribution.version_tuple(
                candidate.version
            ) <= distribution.version_tuple(old.version):
                raise UpdateError(
                    "refusing a downgrade or changed bytes under the same version; publish a new version"
                )
            if not refresh_guard:
                _clean(root)
            print(
                f"relkit update: {PROJECTION}: {old.version} sha256:{old.sha256} -> {candidate.version} sha256:{candidate.sha256}"
            )
            if old_hook is not None:
                print(f"relkit update: owned guard to refresh: {hook}")
                new_digests = protection._guarded_digests(root)
                new_digests[PROJECTION] = candidate.sha256
                new_hook = protection.hook_content(root, digests=new_digests).encode()
                print(
                    f"relkit update: guard sha256:{hashlib.sha256(old_hook).hexdigest()} -> sha256:{hashlib.sha256(new_hook).hexdigest()}"
                )
            if dry_run:
                print(
                    "relkit update: dry run; would refresh only the owned guard"
                    if refresh_guard
                    else "relkit update: dry run; would replace the projection"
                    + (" and refresh its owned guard" if old_hook else "; no guard is installed")
                )
                return 0
            _confirm(yes)
            # Check the verified candidate in its own interpreter before replacing
            # a zipapp that might be running this updater on Windows.
            candidate_path = Path(temporary) / "candidate.pyz"
            candidate_path.write_bytes(payload)
            workspace.remember(candidate_path)
            output = _run(
                [sys.executable, str(candidate_path), "--version"],
                root,
                environment=workspace.environment(),
            )
            if not output.startswith(f"release-kit {candidate.version} "):
                raise UpdateError("candidate runtime version does not match the inspected artifact")
            if not refresh_guard:
                _clean(root)
            _check_inputs(root, inputs)
            if (
                projection.read_bytes() != old_payload
                or (hook.read_bytes() if hook.exists() else None) != old_hook
            ):
                raise UpdateError("projection or guard changed during preparation")
            backup = Path(tempfile.mkdtemp(prefix="relkit-update-", dir=git_dir))
            (backup / "relkit.pyz").write_bytes(old_payload)
            if old_hook is not None:
                (backup / "pre-push").write_bytes(old_hook)
            receipt = {
                "schema": 1,
                "root": str(root),
                "backup": backup.name,
                "state": "pending",
                "old_version": old.version,
                "new_version": candidate.version,
                "old_sha256": old.sha256,
                "new_sha256": candidate.sha256,
                "projection_mode": stat.S_IMODE(projection.stat().st_mode),
                "guard_mode": hook_mode,
                "guard_sha256": protection._sha256(hook) if old_hook is not None else None,
                "inputs": inputs,
            }
            _save_receipt(receipt_path, receipt)
            try:
                if not refresh_guard:
                    _atomic(projection, payload, int(receipt["projection_mode"]))
                audit = [sys.executable, str(candidate_path), "audit", "--root", str(root)]
                if no_download:
                    audit.append("--no-download")
                _run(audit, root, environment=workspace.environment())
                _check_inputs(root, inputs)
                if protection._sha256(_safe_path(projection, root)) != candidate.sha256:
                    raise UpdateError("projection changed during validation")
                if old_hook is not None:
                    _guard(root, git_dir, refresh=True)
                    if hook.read_bytes() != old_hook:
                        raise UpdateError("guard changed during validation")
                    # Write the exact approved pins, never recompute trust from live files.
                    _atomic(hook, new_hook, 0o755)
                    if problem := protection.problem(root):
                        raise UpdateError(problem)
                if protection._sha256(projection) != candidate.sha256:
                    raise UpdateError("projection changed during validation")
                receipt["state"] = "installed"
                _save_receipt(receipt_path, receipt)
            except Exception as error:
                try:
                    _restore(root, git_dir, receipt)
                    receipt["state"] = "rolled-back"
                    _save_receipt(receipt_path, receipt)
                except Exception as restore_error:
                    raise UpdateError(
                        f"update failed: {error}; automatic rollback could not complete: {restore_error}; recovery backup: {backup}"
                    ) from restore_error
                raise UpdateError(
                    f"update failed; previous artifact/guard restored: {error}"
                ) from error
            print(f"relkit update: installed {candidate.version}; backup: {backup}")
            print(
                "relkit update: review the projection diff, run project tests, then commit; Git index and refs were not changed"
            )
            print(
                "relkit update: policy and CI were not changed; new opt-in features require project review"
            )
            return 0
    except (
        UpdateError,
        distribution.DistributionError,
        config.ConfigError,
        protection.ProtectionError,
        storage.StorageError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
    ) as error:
        print(f"relkit update: {error}", file=sys.stderr)
        return 2
    finally:
        if locked and lock is not None:
            try:
                lock.unlink(missing_ok=True)
            except OSError as error:
                print(f"relkit update: could not remove lock {lock}: {error}", file=sys.stderr)
