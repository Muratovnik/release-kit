"""The one publication command adopted by repositories."""

from __future__ import annotations

import sys
from pathlib import Path

from . import config as config_module
from . import engines
from .exposure import audit
from .overlay import manifest as manifest_module
from .overlay import verify as verify_module


def _mounted_private_paths(settings: config_module.Config, root: Path) -> list[str]:
    private_paths = list(settings.exposure.private_paths)
    manifest_path = settings.overlay.manifest_path(root)
    private_root = settings.overlay.private_path(root)
    if manifest_path is None or private_root is None or not manifest_path.is_file():
        return private_paths
    mounts = manifest_module.read(manifest_path)
    for mount in mounts:
        link = mount.link_path(private_root)
        try:
            derived = link.absolute().relative_to(root)
        except ValueError:
            continue
        private_paths.append(derived.as_posix())
    return private_paths


def run(
    root: Path,
    *,
    history: bool,
    staged: bool,
    strict: bool,
    require_overlay: bool,
    allow_download: bool,
) -> int:
    try:
        settings = config_module.load(root)
        private_paths = _mounted_private_paths(settings, root)
    except (config_module.ConfigError, manifest_module.ManifestError) as error:
        print(f"relkit audit: {error}", file=sys.stderr)
        return 2

    names = settings.exposure.names(root)
    report = audit.scan(
        root,
        names=names,
        baseline=settings.exposure.baseline,
        exclude=settings.exposure.exclude,
        private_paths=private_paths,
        private_files=settings.exposure.private_files,
        private_suffixes=settings.exposure.private_suffixes,
        required_ignores=settings.exposure.required_ignores,
        forbidden_suffixes=settings.exposure.forbidden_suffixes,
        allowed_users=settings.exposure.allowed_users,
        forbid_png_metadata=settings.exposure.forbid_png_metadata,
        include_candidates=settings.exposure.include_candidates and not staged,
        staged=staged,
    )
    failures = list(report.failures)
    if history:
        try:
            changes = audit.worktree_changes(root)
        except RuntimeError as error:
            print(f"relkit audit: {error}", file=sys.stderr)
            return 2
        if changes:
            failures.append(
                "history audit requires a clean worktree; use --staged before committing"
            )
    if strict:
        failures.extend(str(finding) for finding in report.baselined)
    if history:
        failures.extend(
            audit.history_failures(
                root,
                names=names,
                private_paths=private_paths,
                private_files=settings.exposure.private_files,
                private_suffixes=settings.exposure.private_suffixes,
                forbidden_suffixes=settings.exposure.forbidden_suffixes,
                allowed_users=settings.exposure.allowed_users,
                allowed_identities=settings.exposure.allowed_identities,
                exclude=settings.exposure.exclude,
            )
        )

    overlay_root = settings.overlay.private_path(root)
    overlay_manifest = settings.overlay.manifest_path(root)
    if overlay_root is not None and overlay_manifest is not None:
        if overlay_root.is_dir() and overlay_manifest.is_file():
            try:
                mounts = manifest_module.read(overlay_manifest)
                problems, skipped = verify_module.check(
                    mounts, public_root=root, private_root=overlay_root
                )
            except manifest_module.ManifestError as error:
                print(f"relkit audit: {error}", file=sys.stderr)
                return 2
            failures.extend(str(problem) for problem in problems)
            failures.extend(
                f"overlay mount not owned by this repository: {item}" for item in skipped
            )
        elif require_overlay:
            failures.append(f"private overlay is unavailable: {overlay_root}")
    elif require_overlay:
        failures.append("[overlay] is not configured")

    for item in report.unreadable:
        failures.append(f"could not read {item}")
    if report.baselined:
        print(f"relkit audit: {len(report.baselined)} baselined finding(s) remain")

    engine_failures: list[str] = []
    try:
        if settings.exposure.check_secrets and engines.betterleaks(
            root,
            config=settings.exposure.betterleaks_config,
            history=history,
            staged=staged,
            allow_download=allow_download,
        ):
            engine_failures.append("Betterleaks failed")
        if settings.exposure.check_links and engines.lychee(
            root,
            staged=staged,
            include_candidates=settings.exposure.include_candidates,
            allow_download=allow_download,
        ):
            engine_failures.append("Lychee failed")
    except (OSError, RuntimeError) as error:
        print(f"relkit audit: {error}", file=sys.stderr)
        return 2

    failures.extend(engine_failures)
    if failures:
        print("relkit audit: failed", file=sys.stderr)
        for failure in sorted(dict.fromkeys(failures)):
            print(f"  {failure}", file=sys.stderr)
        return 1
    print("relkit audit: passed" + (" (including history)" if history else ""))
    return 0
