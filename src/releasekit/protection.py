"""Install and verify the owner-side pre-push publication guard."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

from . import config as config_module

MARKER = "# managed by release-kit: owner publication guard v1"
DISPATCHER_MARKER = "# managed by release-kit: shared pre-push dispatcher v1"
PROJECTION_PATH = ".github/relkit.pyz"
DISPATCHER = f"""#!/bin/sh
{DISPATCHER_MARKER}
set -eu
root=$(git rev-parse --show-toplevel) || exit 2
common=$(git rev-parse --git-common-dir) || exit 2
case "$common" in
    /*|[A-Za-z]:[\\/]*) ;;
    *) common="$root/$common" ;;
esac
guard="$common/hooks/pre-push"
if [ -x "$guard" ]; then
    exec "$guard" "$@"
fi
exit 0
"""


class ProtectionError(Exception):
    """The managed hook cannot be installed without clobbering another owner."""


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _guarded_digests(root: Path) -> dict[str, str]:
    try:
        settings = config_module.load(root).exposure
    except config_module.ConfigError as error:
        raise ProtectionError(str(error)) from error
    relatives = [config_module.CONFIG_NAME, PROJECTION_PATH]
    if settings.check_secrets:
        relatives.append(settings.betterleaks_config)
    answer: dict[str, str] = {}
    for relative in dict.fromkeys(relatives):
        path = root / relative
        if not path.is_file():
            raise ProtectionError(f"guarded publication input is unavailable: {path}")
        try:
            answer[relative] = _sha256(path)
        except OSError as error:
            raise ProtectionError(f"guarded publication input could not be read: {path}") from error
    return answer


def hook_content(root: Path) -> str:
    expected = repr(_guarded_digests(root))
    return f"""#!/bin/sh
{MARKER}
root=$(git rev-parse --show-toplevel) || exit 2
python - "$root" <<'PY' || exit 2
import hashlib
import pathlib
import sys

root = pathlib.Path(sys.argv[1])
expected = {expected}
for relative, digest in expected.items():
    path = root / relative
    try:
        observed = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError:
        observed = ""
    if observed != digest:
        print(
            f"release-kit guarded input changed: {{relative}}; "
            "review it and run `relkit protect install`",
            file=sys.stderr,
        )
        raise SystemExit(2)
PY
exec python "$root/{PROJECTION_PATH}" audit --history --owner
"""


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
    temporary = path.with_name(path.name + ".relkit-partial")
    try:
        temporary.write_text(content, encoding="utf-8", newline="\n")
        os.chmod(temporary, 0o755)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


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
        return f"owner pre-push guard has drifted: {path}"
    if not os.access(path, os.X_OK):
        return f"owner pre-push guard is not executable: {path}"
    try:
        effective = effective_hook_path(root)
        if effective != path:
            dispatcher = effective.read_text(encoding="utf-8")
            if dispatcher.replace("\r\n", "\n") != DISPATCHER:
                return f"shared pre-push dispatcher has drifted: {effective}"
            if not os.access(effective, os.X_OK):
                return f"shared pre-push dispatcher is not executable: {effective}"
    except (OSError, ProtectionError):
        return "shared pre-push dispatcher is not installed; run `relkit protect install`"
    return None


def install(root: Path) -> Path:
    path = hook_path(root)
    _write_managed(path, hook_content(root), MARKER, "repository pre-push hook")
    effective = effective_hook_path(root)
    if effective != path:
        _write_managed(
            effective,
            DISPATCHER,
            DISPATCHER_MARKER,
            "shared pre-push dispatcher",
        )
    return path
