"""Install and verify the owner-side guards: pre-push for publication, commit-msg per commit."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import canonical, distribution, storage
from . import config as config_module

MARKER = "# managed by release-kit: owner publication guard v1"
COMPATIBLE_DISPATCHER_MARKER = "# git-common-dir-hook-dispatcher: pre-push v1"
PROJECTION_PATH = ".github/relkit.pyz"
# The release that added `audit --push`, which the current guard template calls. A guard
# pinned to an older projection would fail every push with a usage error.
PUSH_AUDIT_VERSION = "0.33.0"
COMMIT_MARKER = "# managed by release-kit: owner commit guard v1"
COMMIT_DISPATCHER_MARKER = "# git-common-dir-hook-dispatcher: commit-msg v1"
# The release that added `audit --commit`, which the commit guard runs.
COMMIT_AUDIT_VERSION = "0.34.0"
# The projection copy the commit guard runs, beside the hooks in the common Git
# directory. Every branch and worktree is then judged by the code the owner installed,
# not by whatever projection it has checked out: a branch still on an older release
# would otherwise refuse each commit over an option its projection does not know.
COMMIT_COPY = "relkit-commit-guard.pyz"
# Pinned in place of a digest for an optional input that is not there: the guard then
# refuses the push if the file appears, exactly as it does when a pinned file changes.
ABSENT = ""


class ProtectionError(Exception):
    """The managed hook cannot be installed without clobbering another owner."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def push_audit_problem(version: str) -> str | None:
    """Why a projection of this version cannot run the current guard, or None."""
    if distribution.version_tuple(version) >= distribution.version_tuple(PUSH_AUDIT_VERSION):
        return None
    return f"release-kit {version} has no `audit --push` (added in {PUSH_AUDIT_VERSION})"


def projection_problem(root: Path) -> str | None:
    """Whether the pinned projection can run the guard `protect install` would write.

    A file that is not an inspectable release-kit zipapp is not judged here: the guard
    runs it on every push and fails closed, which names the file as the problem.
    """
    try:
        found = distribution.inspect((root / PROJECTION_PATH).read_bytes())
    except (OSError, distribution.DistributionError):
        return None
    return push_audit_problem(found.version)


def guarded_inputs(policy: config_module.Config) -> list[str]:
    """Repository files the guard pins before it runs repository-controlled code."""
    relatives = [config_module.CONFIG_NAME, PROJECTION_PATH]
    if policy.exposure.check_secrets:
        relatives.append(policy.exposure.betterleaks_config)
    if policy.release is not None and policy.release.workflow:
        # The tag workflow is the file that actually publishes. A changed upload
        # step must be reviewed against the declared exact asset set before a
        # push, not discovered after the immutable release exists. A local
        # publisher has no workflow: the coordinator delivers the prepared files
        # itself, and an empty name would pin the repository root instead.
        relatives.append(policy.release.workflow)
    return list(dict.fromkeys(relatives))


def optional_inputs(policy: config_module.Config) -> frozenset[str]:
    """Guarded files that may be absent: without a secret policy the engine default applies."""
    if policy.exposure.check_secrets:
        return frozenset({policy.exposure.betterleaks_config})
    return frozenset()


def _guarded_digests(root: Path) -> dict[str, str]:
    try:
        policy = config_module.load(root)
    except config_module.ConfigError as error:
        raise ProtectionError(str(error)) from error
    optional = optional_inputs(policy)
    answer: dict[str, str] = {}
    for relative in guarded_inputs(policy):
        path = root / relative
        if relative in optional and not os.path.lexists(path):
            answer[relative] = ABSENT
            continue
        if not path.is_file():
            raise ProtectionError(
                f"guarded publication input is unavailable: {path}; create it or change the "
                f"relkit.toml setting that names {relative}"
            )
        try:
            answer[relative] = _sha256(path)
        except OSError as error:
            raise ProtectionError(f"guarded publication input could not be read: {path}") from error
    return answer


# The verification body is identical in every template version: only the shell that
# selects an interpreter has changed. Keeping it in one place keeps the historical
# templates below honest, and they must never be edited again.
_HOOK_BODY_HEAD = """import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = """

_HOOK_BODY_TAIL = """
for relative, digest in expected.items():
    path = root / relative
    try:
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        observed = ""
    if observed != digest:
        print(
            f"release-kit guarded input changed: {relative}; "
            "review it and run `relkit protect install`",
            file=sys.stderr,
        )
        raise SystemExit(2)
"""


def _hook_v1(expected: str) -> str:
    """Through 0.14.0. Frozen so an older installed guard is still recognized."""
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "root=$(git rev-parse --show-toplevel) || exit 2\n"
        "python - \"$root\" <<'PY' || exit 2\n"
        + _HOOK_BODY_HEAD
        + expected
        + _HOOK_BODY_TAIL
        + "PY\n"
        f'exec python "$root/{PROJECTION_PATH}" audit --history --owner\n'
    )


# `python` is not on every PATH, and a Windows Store alias is not an interpreter: probe
# the candidates instead of failing with a shell error that names neither release-kit
# nor the missing runtime. Its bytes are part of every template since the second.
_INTERPRETER_PROBE = (
    "relkit_python=\n"
    "for relkit_candidate in python python3 py; do\n"
    '  if command -v "$relkit_candidate" >/dev/null 2>&1 &&'
    ' "$relkit_candidate" -c "" >/dev/null 2>&1; then\n'
    "    relkit_python=$relkit_candidate\n"
    "    break\n"
    "  fi\n"
    "done\n"
    'if [ -z "$relkit_python" ]; then\n'
    '  echo "release-kit guard needs a working python, python3 or py on PATH" >&2\n'
    "  exit 2\n"
    "fi\n"
)


def _hook_with_interpreter_probe(expected: str, audit_arguments: str) -> str:
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "root=$(git rev-parse --show-toplevel) || exit 2\n"
        + _INTERPRETER_PROBE
        + '"$relkit_python" - "$root" <<\'PY\' || exit 2\n'
        + _HOOK_BODY_HEAD
        + expected
        + _HOOK_BODY_TAIL
        + "PY\n"
        f'exec "$relkit_python" "$root/{PROJECTION_PATH}" audit {audit_arguments}\n'
    )


def _hook_v2(expected: str) -> str:
    """Through 0.32.0: the whole history, which needs a clean tracked worktree and reads
    every remote-tracking ref, so unrelated work could refuse a clean push. Frozen."""
    return _hook_with_interpreter_probe(expected, "--history --owner")


def _hook_v3(expected: str) -> str:
    """Current: only what the push publishes. Git writes the refs being updated to the
    hook's standard input, which this shell leaves untouched for the audit, and names
    the destination remote as its first argument. The whole history is still judged by
    an explicit `audit --history` and by the release."""
    return _hook_with_interpreter_probe(expected, '--push --owner --remote "$1"')


# Newest first. An installed guard from any of these is intact; only the newest is
# ever written, so `protect install` migrates a repository forward.
HOOK_TEMPLATES = (_hook_v3, _hook_v2, _hook_v1)


def hook_content(root: Path, *, digests: dict[str, str] | None = None) -> str:
    return _hook_v3(repr(_guarded_digests(root) if digests is None else digests))


def _git(root: Path, arguments: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *arguments],
            cwd=root,
            check=False,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except subprocess.TimeoutExpired as error:
        raise ProtectionError(f"git {' '.join(arguments)} timed out after 30s in {root}") from error
    except OSError as error:
        raise ProtectionError(f"Git hook lookup could not run: {error}") from error


def hook_path(root: Path, name: str = "pre-push") -> Path:
    # The workstation may use a global hooksPath dispatcher. Repository-owned hooks
    # still live in the common Git directory; the dispatcher and ordinary Git both
    # use this stable location, without release-kit replacing somebody else's hook.
    result = _git(root, ["rev-parse", "--git-common-dir"])
    if result.returncode:
        raise ProtectionError(result.stderr.strip() or "Git hook path is unavailable")
    common = Path(result.stdout.strip())
    common = common if common.is_absolute() else root / common
    return (common / "hooks" / name).resolve()


def effective_hook_path(root: Path, name: str = "pre-push") -> Path:
    result = _git(root, ["rev-parse", "--git-path", f"hooks/{name}"])
    if result.returncode:
        raise ProtectionError(result.stderr.strip() or "effective Git hook path is unavailable")
    path = Path(result.stdout.strip())
    return (path if path.is_absolute() else root / path).resolve()


def _replace(path: Path, payload: bytes) -> None:
    """Write a file whole or not at all, executable as a hook needs to be."""
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".relkit-hook-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
        os.chmod(temporary, 0o755)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _unmanaged(path: Path, content: str, marker: str) -> bool:
    """A hook at this path that release-kit did not write and must not replace."""
    if not path.exists():
        return False
    existing = path.read_text(encoding="utf-8", errors="replace")
    return marker not in existing and existing.replace("\r\n", "\n") != content


def _write_managed(path: Path, content: str, marker: str, description: str) -> None:
    if _unmanaged(path, content, marker):
        raise ProtectionError(
            f"refusing to replace an unmanaged {description}: {path}; "
            "move it aside or fold its checks into a compatible dispatcher, then retry"
        )
    _replace(path, content.encode("utf-8"))


def _dispatcher_problem(
    path: Path, name: str = "pre-push", marker: str = COMPATIBLE_DISPATCHER_MARKER
) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            f"compatible {name} dispatcher is not installed at {path}; "
            f"install an executable dispatcher containing `{marker}`"
        )
    except OSError as error:
        return f"compatible {name} dispatcher cannot be read at {path}: {error}"
    if marker not in text.replace("\r\n", "\n").splitlines():
        return (
            f"effective {name} hook is not a compatible repository dispatcher: {path}; "
            f"expected `{marker}`"
        )
    if not os.access(path, os.X_OK):
        return f"compatible {name} dispatcher is not executable: {path}"
    return None


def _recorded(text: str) -> tuple[dict[str, str], bool] | None:
    """The pins of an intact known template, and whether it is the current one."""
    lines = [
        line.removeprefix("expected = ")
        for line in text.splitlines()
        if line.startswith("expected = ")
    ]
    try:
        recorded = ast.literal_eval(lines[0]) if len(lines) == 1 else None
    except (ValueError, SyntaxError):
        recorded = None
    if not isinstance(recorded, dict) or not all(
        isinstance(path, str) and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}|", digest)
        for path, digest in recorded.items()
    ):
        return None
    expected = repr(recorded)
    for index, template in enumerate(HOOK_TEMPLATES):
        if template(expected) == text:
            return recorded, index == 0
    return None


def recorded_digests(root: Path) -> dict[str, str]:
    """Read only a complete known guard template, never evaluate its embedded code."""
    text = hook_path(root).read_text(encoding="utf-8").replace("\r\n", "\n")
    if (found := _recorded(text)) is None:
        raise ProtectionError(
            f"pre-push hook {hook_path(root)} is not an intact supported release-kit guard "
            "template; review it, then run `relkit protect install`"
        )
    return found[0]


def outdated_template(root: Path) -> bool:
    """An intact guard written by an older release-kit; its pins are still enforced."""
    try:
        found = _recorded(hook_path(root).read_text(encoding="utf-8").replace("\r\n", "\n"))
    except (ProtectionError, OSError, UnicodeError):
        return False
    return found is not None and not found[1]


def digest_changes(root: Path) -> list[str]:
    recorded = recorded_digests(root)
    current = _guarded_digests(root)
    return [
        f"{path}: pinned {_shown(recorded.get(path), '<not pinned>')} -> "
        f"current {_shown(current.get(path), '<not guarded>')}"
        for path in sorted(recorded.keys() | current.keys())
        if recorded.get(path) != current.get(path)
    ]


def _shown(digest: str | None, missing: str) -> str:
    if digest is None:
        return missing
    return digest or "<absent>"


def problem(root: Path) -> str | None:
    try:
        path = hook_path(root)
        text = path.read_text(encoding="utf-8")
        expected_hook = hook_content(root)
    except ProtectionError as error:
        return f"owner pre-push guard cannot be verified: {error}"
    except OSError:
        return f"owner pre-push guard is not installed at {path}; run `relkit protect install`"
    if text.replace("\r\n", "\n") != expected_hook:
        try:
            changes = digest_changes(root)
        except (ProtectionError, OSError, UnicodeError) as error:
            return f"owner pre-push guard has drifted: {path}; {error}; manual owner review is required"
        if changes:
            return (
                f"owner pre-push guard has drifted: {path}\n  "
                + "\n  ".join(changes)
                + "\nReview these exact inputs and obtain any project-required hook permission; "
                "then run `relkit update --refresh-guard` (or `relkit protect install`)."
            )
        # An intact older template may keep its pins, but Git must still be able
        # to execute it through the currently configured dispatcher.
    if not os.access(path, os.X_OK):
        return f"owner pre-push guard is not executable: {path}; run `relkit protect install`"
    try:
        effective = effective_hook_path(root)
    except ProtectionError as error:
        return f"effective pre-push dispatcher cannot be verified: {error}"
    if effective != path and (dispatcher_problem := _dispatcher_problem(effective)):
        return dispatcher_problem
    return None


def stale_plan_message(reviewed: str, plan: dict) -> str:
    return (
        f"reviewed hook plan {reviewed[:12]} no longer matches the current plan "
        f"{plan['plan_sha256'][:12]} for {plan['path']}; run `relkit protect install "
        "--dry-run`, review it, and pass its plan_sha256 as --plan-hash"
    )


def install_plan(root: Path) -> dict:
    """Review owned hook bytes and inputs without installing them."""
    try:
        storage.inside(root, root / ".git/hooks/pre-push")
        path = hook_path(root)
        storage.inside(root, path)
    except storage.StorageError as error:
        raise ProtectionError(str(error)) from error
    effective = effective_hook_path(root)
    if effective != path and (dispatcher_problem := _dispatcher_problem(effective)):
        raise ProtectionError(dispatcher_problem)
    if problem := projection_problem(root):
        raise ProtectionError(
            f"the pinned {PROJECTION_PATH} cannot run the guard: {problem}; the guard would "
            f"refuse every push. Run `relkit update` first to move the projection to "
            f"{PUSH_AUDIT_VERSION} or newer, then run `relkit protect install` again"
        )
    inputs = _guarded_digests(root)
    for relative in inputs:
        try:
            storage.inside(root, root / relative)
        except storage.StorageError as error:
            raise ProtectionError(str(error)) from error
    content = hook_content(root, digests=inputs)
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
        if MARKER not in existing and existing.replace("\r\n", "\n") != content:
            raise ProtectionError(
                f"refusing to replace an unmanaged repository pre-push hook: {path}; "
                "move it aside or fold its checks into a compatible dispatcher, then retry"
            )
    plan = {
        "root": str(root),
        "path": str(path),
        "before_sha256": _sha256(path) if path.exists() else None,
        "after_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "inputs": inputs,
        "effective_hook": str(effective),
        "dispatcher_sha256": _sha256(effective) if effective != path else None,
        "commit_guard": commit_guard_plan(root),
    }
    return {**plan, "plan_sha256": canonical.fingerprint(plan)}


def install(root: Path, *, plan_hash: str = "") -> Path:
    plan = install_plan(root)
    if plan_hash and plan_hash != plan["plan_sha256"]:
        raise ProtectionError(stale_plan_message(plan_hash, plan))
    path = Path(plan["path"])
    _write_managed(
        path, hook_content(root, digests=plan["inputs"]), MARKER, "repository pre-push hook"
    )
    commit = plan["commit_guard"]
    if commit["status"] == "install":
        payload = (root / PROJECTION_PATH).read_bytes()
        if hashlib.sha256(payload).hexdigest() != commit["copy_after_sha256"]:
            raise ProtectionError(
                f"{PROJECTION_PATH} changed while the guards were installed; review it and "
                "run `relkit protect install` again"
            )
        # The copy first: a guard that finds no copy refuses the commit and names it.
        _replace(Path(commit["copy_path"]), payload)
        _write_managed(
            Path(commit["path"]),
            commit_hook_content(),
            COMMIT_MARKER,
            "repository commit-msg hook",
        )
    return path


def commit_hook_content() -> str:
    """The commit guard. It pins nothing: the copy it runs is the reviewed code, and the
    owner policy it applies lives outside the repository, so no branch can change it."""
    return (
        "#!/bin/sh\n"
        f"{COMMIT_MARKER}\n"
        "root=$(git rev-parse --show-toplevel) || exit 2\n"
        "common=$(git rev-parse --path-format=absolute --git-common-dir) || exit 2\n"
        f'copy="$common/hooks/{COMMIT_COPY}"\n'
        'if [ ! -f "$copy" ]; then\n'
        '  echo "release-kit commit guard: $copy is missing; run relkit protect install" >&2\n'
        "  exit 2\n"
        "fi\n"
        + _INTERPRETER_PROBE
        # Silent when the commit passes; a refusal and its fix are written to stderr.
        + 'exec "$relkit_python" "$copy" audit --commit --owner --root "$root" --message "$1"'
        " >/dev/null\n"
    )


def _projection_version(payload: bytes) -> str | None:
    """The release-kit version a projection declares, or None when it is not inspectable."""
    try:
        return distribution.inspect(payload).version
    except distribution.DistributionError:
        return None


def commit_guard_plan(root: Path) -> dict:
    """What `protect install` does to the commit guard, and why when it does nothing.

    The commit guard is the fast early warning; the pre-push guard stays the gate. So a
    commit-msg hook that belongs to someone else, or a projection too old to run the
    check, leaves the commit guard out with a reason instead of refusing the install.
    """
    try:
        path = hook_path(root, "commit-msg")
        copy = path.parent / COMMIT_COPY
        storage.inside(root, path)
        storage.inside(root, copy)
    except storage.StorageError as error:
        raise ProtectionError(str(error)) from error
    content = commit_hook_content()
    plan: dict = {
        "path": str(path),
        "copy_path": str(copy),
        "status": "install",
        "reason": None,
        "before_sha256": _sha256(path) if path.exists() else None,
        "after_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "copy_before_sha256": _sha256(copy) if copy.exists() else None,
        "copy_after_sha256": None,
    }
    try:
        payload = (root / PROJECTION_PATH).read_bytes()
    except OSError as error:
        return {**plan, "status": "skipped", "reason": f"{PROJECTION_PATH} is unreadable: {error}"}
    version = _projection_version(payload)
    # A file that is not an inspectable release-kit archive is not judged here, as for
    # the pre-push guard: the commit guard runs it and fails closed if it cannot.
    if version is not None and distribution.version_tuple(version) < distribution.version_tuple(
        COMMIT_AUDIT_VERSION
    ):
        return {
            **plan,
            "status": "skipped",
            "reason": (
                f"release-kit {version} has no `audit --commit` (added in "
                f"{COMMIT_AUDIT_VERSION}); run `relkit update`, then `relkit protect install`"
            ),
        }
    if _unmanaged(path, content, COMMIT_MARKER):
        return {
            **plan,
            "status": "skipped",
            "reason": (
                f"{path} is a commit-msg hook release-kit did not write; call "
                '`relkit audit --commit --owner --message "$1"` from it, or move it aside and '
                "run `relkit protect install` again"
            ),
        }
    return {**plan, "copy_after_sha256": hashlib.sha256(payload).hexdigest()}


def commit_guard_problem(root: Path) -> str | None:
    """Why commits are not being checked against the owner policy, or None.

    An adopter's own commit-msg hook that runs `audit --commit` is accepted: chaining the
    check into an existing hook is the documented alternative to the managed guard.
    """
    try:
        path = hook_path(root, "commit-msg")
        effective = effective_hook_path(root, "commit-msg")
    except ProtectionError as error:
        return f"owner commit guard cannot be verified: {error}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace").replace("\r\n", "\n")
    except FileNotFoundError:
        return f"owner commit guard is not installed at {path}; run `relkit protect install`"
    except OSError as error:
        return f"owner commit guard cannot be read at {path}: {error}"
    if COMMIT_MARKER in text:
        if text != commit_hook_content():
            return f"owner commit guard has drifted: {path}; run `relkit protect install`"
        copy = path.parent / COMMIT_COPY
        try:
            version = _projection_version(copy.read_bytes())
        except OSError:
            return f"owner commit guard runs {copy}, which is missing; run `relkit protect install`"
        if version is not None and distribution.version_tuple(version) < distribution.version_tuple(
            COMMIT_AUDIT_VERSION
        ):
            return (
                f"owner commit guard runs release-kit {version}, which has no `audit --commit`; "
                "run `relkit protect install`"
            )
    elif "audit --commit" not in text:
        return (
            f"commit-msg hook {path} does not run `relkit audit --commit`; commits are not "
            "checked against the owner policy"
        )
    if not os.access(path, os.X_OK):
        return f"owner commit guard is not executable: {path}; run `relkit protect install`"
    if effective != path and (
        problem := _dispatcher_problem(effective, "commit-msg", COMMIT_DISPATCHER_MARKER)
    ):
        return f"{problem}; Git may never run the commit guard at {path}"
    return None
