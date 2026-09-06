"""Install and verify the owner-side pre-push publication guard."""

from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import tempfile
from pathlib import Path

from . import canonical, storage
from . import config as config_module

MARKER = "# managed by release-kit: owner publication guard v1"
COMPATIBLE_DISPATCHER_MARKER = "# git-common-dir-hook-dispatcher: pre-push v1"
PROJECTION_PATH = ".github/relkit.pyz"


class ProtectionError(Exception):
    """The managed hook cannot be installed without clobbering another owner."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def guarded_inputs(policy: config_module.Config) -> list[str]:
    """Repository files the guard pins before it runs repository-controlled code."""
    relatives = [config_module.CONFIG_NAME, PROJECTION_PATH]
    if policy.exposure.check_secrets:
        relatives.append(policy.exposure.betterleaks_config)
    if policy.release is not None:
        # The tag workflow is the file that actually publishes. A changed upload
        # step must be reviewed against the declared exact asset set before a
        # push, not discovered after the immutable release exists.
        relatives.append(policy.release.workflow)
    return list(dict.fromkeys(relatives))


def _guarded_digests(root: Path) -> dict[str, str]:
    try:
        policy = config_module.load(root)
    except config_module.ConfigError as error:
        raise ProtectionError(str(error)) from error
    answer: dict[str, str] = {}
    for relative in guarded_inputs(policy):
        path = root / relative
        if not path.is_file():
            raise ProtectionError(f"guarded publication input is unavailable: {path}")
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


def _hook_v2(expected: str) -> str:
    """Current. `python` is not on every PATH, and a Windows Store alias is not an
    interpreter: probe the candidates instead of failing the push with a shell error
    that names neither release-kit nor the missing runtime."""
    return (
        "#!/bin/sh\n"
        f"{MARKER}\n"
        "root=$(git rev-parse --show-toplevel) || exit 2\n"
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
        '"$relkit_python" - "$root" <<\'PY\' || exit 2\n'
        + _HOOK_BODY_HEAD
        + expected
        + _HOOK_BODY_TAIL
        + "PY\n"
        f'exec "$relkit_python" "$root/{PROJECTION_PATH}" audit --history --owner\n'
    )


# Newest first. An installed guard from any of these is intact; only the newest is
# ever written, so `protect install` migrates a repository forward.
HOOK_TEMPLATES = (_hook_v2, _hook_v1)


def hook_content(root: Path, *, digests: dict[str, str] | None = None) -> str:
    return _hook_v2(repr(_guarded_digests(root) if digests is None else digests))


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
        raise ProtectionError("Git hook lookup timed out") from error
    except OSError as error:
        raise ProtectionError(f"Git hook lookup could not run: {error}") from error


def hook_path(root: Path) -> Path:
    # The workstation may use a global hooksPath dispatcher. Repository-owned hooks
    # still live in the common Git directory; the dispatcher and ordinary Git both
    # use this stable location, without release-kit replacing somebody else's hook.
    result = _git(root, ["rev-parse", "--git-common-dir"])
    if result.returncode:
        raise ProtectionError(result.stderr.strip() or "Git hook path is unavailable")
    common = Path(result.stdout.strip())
    common = common if common.is_absolute() else root / common
    return (common / "hooks" / "pre-push").resolve()


def effective_hook_path(root: Path) -> Path:
    result = _git(root, ["rev-parse", "--git-path", "hooks/pre-push"])
    if result.returncode:
        raise ProtectionError(result.stderr.strip() or "effective Git hook path is unavailable")
    path = Path(result.stdout.strip())
    return (path if path.is_absolute() else root / path).resolve()


def _write_managed(path: Path, content: str, marker: str, description: str) -> None:
    if path.exists():
        existing = path.read_text(encoding="utf-8", errors="replace")
        if marker not in existing and existing.replace("\r\n", "\n") != content:
            raise ProtectionError(f"refusing to replace an unmanaged {description}: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix=".relkit-hook-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
        os.chmod(temporary, 0o755)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _dispatcher_problem(path: Path) -> str | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return (
            f"compatible pre-push dispatcher is not installed at {path}; "
            f"install an executable dispatcher containing `{COMPATIBLE_DISPATCHER_MARKER}`"
        )
    except OSError as error:
        return f"compatible pre-push dispatcher cannot be read at {path}: {error}"
    if COMPATIBLE_DISPATCHER_MARKER not in text.replace("\r\n", "\n").splitlines():
        return (
            f"effective pre-push hook is not a compatible repository dispatcher: {path}; "
            f"expected `{COMPATIBLE_DISPATCHER_MARKER}`"
        )
    if not os.access(path, os.X_OK):
        return f"compatible pre-push dispatcher is not executable: {path}"
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
        isinstance(path, str) and isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
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
        raise ProtectionError("pre-push hook is not an intact supported release-kit guard template")
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
        f"{path}: pinned {recorded.get(path, '<not pinned>')} -> current {current.get(path, '<not guarded>')}"
        for path in sorted(recorded.keys() | current.keys())
        if recorded.get(path) != current.get(path)
    ]


def problem(root: Path) -> str | None:
    try:
        path = hook_path(root)
        text = path.read_text(encoding="utf-8")
        expected_hook = hook_content(root)
    except ProtectionError as error:
        return f"owner pre-push guard cannot be verified: {error}"
    except OSError:
        return "owner pre-push guard is not installed; run `relkit protect install`"
    if text.replace("\r\n", "\n") != expected_hook:
        try:
            changes = digest_changes(root)
        except (ProtectionError, OSError, UnicodeError) as error:
            return f"owner pre-push guard has drifted: {path}; {error}; manual owner review is required"
        if not changes:
            # An intact older template pinning exactly the current inputs enforces the
            # same thing the current one does. Failing every push and every update on
            # the release that changes the template is how a gate gets switched off.
            return None
        return (
            f"owner pre-push guard has drifted: {path}\n  "
            + "\n  ".join(changes)
            + "\nReview these exact inputs and obtain any project-required hook permission; "
            "then run `relkit update --refresh-guard` (or `relkit protect install`)."
        )
    if not os.access(path, os.X_OK):
        return f"owner pre-push guard is not executable: {path}"
    try:
        effective = effective_hook_path(root)
    except ProtectionError as error:
        return f"effective pre-push dispatcher cannot be verified: {error}"
    if effective != path and (dispatcher_problem := _dispatcher_problem(effective)):
        return dispatcher_problem
    return None


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
                f"refusing to replace an unmanaged repository pre-push hook: {path}"
            )
    plan = {
        "root": str(root),
        "path": str(path),
        "before_sha256": _sha256(path) if path.exists() else None,
        "after_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "inputs": inputs,
        "effective_hook": str(effective),
        "dispatcher_sha256": _sha256(effective) if effective != path else None,
    }
    return {**plan, "plan_sha256": canonical.fingerprint(plan)}


def install(root: Path, *, plan_hash: str = "") -> Path:
    plan = install_plan(root)
    if plan_hash and plan_hash != plan["plan_sha256"]:
        raise ProtectionError("reviewed hook plan is stale; review a new dry run")
    path = Path(plan["path"])
    _write_managed(
        path, hook_content(root, digests=plan["inputs"]), MARKER, "repository pre-push hook"
    )
    return path
