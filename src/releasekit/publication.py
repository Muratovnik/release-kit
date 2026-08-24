"""The one publication command adopted by repositories."""

from __future__ import annotations

import sys
from pathlib import Path

from . import config as config_module
from . import engines, owner, protection
from .exposure import audit
from .overlay import manifest as manifest_module
from .overlay import verify as verify_module


def _mounted_private_paths(
    settings: config_module.Config,
    root: Path,
    policy: owner.OwnerPolicy | None,
) -> list[str]:
    private_paths = list(settings.exposure.private_paths)
    if policy is None or not policy.manifest_path.is_file():
        return private_paths
    mounts = manifest_module.read(policy.manifest_path)
    for mount in mounts:
        link = mount.link_path(policy.root)
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
    owner_mode: bool,
    require_overlay: bool,
    allow_download: bool,
) -> int:
    try:
        settings = config_module.load(root)
        policy = owner.discover(root) if owner_mode or require_overlay else None
        owner_rules = policy.load() if policy is not None else owner.OwnerRules()
        names = owner_rules.private_values
        providers = {
            name: provider.allowed_surfaces
            for name, provider in settings.exposure.providers.items()
        }
        workflow_names = {name.casefold() for name in owner_rules.owner_workflows}
        overlap = sorted(name for name in providers if name.casefold() in workflow_names)
        if overlap:
            raise owner.OwnerPolicyError(
                "product provider is also declared as owner workflow: " + ", ".join(overlap)
            )
        private_paths = _mounted_private_paths(settings, root, policy)
    except (
        config_module.ConfigError,
        manifest_module.ManifestError,
        owner.OwnerPolicyError,
    ) as error:
        print(f"relkit audit: {error}", file=sys.stderr)
        return 2

    report = audit.scan(
        root,
        names=names,
        owner_workflows=owner_rules.owner_workflows,
        private_patterns=owner_rules.patterns,
        baseline=settings.exposure.baseline,
        exclude=settings.exposure.exclude,
        private_paths=private_paths,
        private_files=settings.exposure.private_files,
        private_suffixes=settings.exposure.private_suffixes,
        required_ignores=settings.exposure.required_ignores,
        forbidden_suffixes=settings.exposure.forbidden_suffixes,
        allowed_users=settings.exposure.allowed_users,
        forbid_png_metadata=settings.exposure.forbid_png_metadata,
        forbid_ai_attribution=settings.exposure.forbid_ai_attribution,
        forbid_internal_planning=settings.exposure.forbid_internal_planning,
        forbid_machine_observations=settings.exposure.forbid_machine_observations,
        providers=providers,
        provenance_required=settings.exposure.provenance_required,
        provenance=settings.exposure.provenance,
        inspect_archives=settings.exposure.inspect_archives,
        include_candidates=settings.exposure.include_candidates and not staged,
        staged=staged,
    )
    failures = list(report.failures)
    if policy is not None and (guard_problem := protection.problem(root)):
        failures.append(guard_problem)
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
                owner_workflows=owner_rules.owner_workflows,
                private_patterns=owner_rules.patterns,
                private_paths=private_paths,
                private_files=settings.exposure.private_files,
                private_suffixes=settings.exposure.private_suffixes,
                forbidden_suffixes=settings.exposure.forbidden_suffixes,
                allowed_users=settings.exposure.allowed_users,
                allowed_identities=settings.exposure.allowed_identities,
                exclude=settings.exposure.exclude,
                forbid_ai_attribution=settings.exposure.forbid_ai_attribution,
                forbid_internal_planning=settings.exposure.forbid_internal_planning,
                forbid_machine_observations=settings.exposure.forbid_machine_observations,
                providers=providers,
                provenance_required=settings.exposure.provenance_required,
                provenance=settings.exposure.provenance,
                inspect_archives=settings.exposure.inspect_archives,
            )
        )

    if policy is not None and policy.manifest_path.is_file():
        if policy.root.is_dir():
            try:
                mounts = manifest_module.read(policy.manifest_path)
                problems, skipped = verify_module.check(
                    mounts, public_root=root, private_root=policy.root
                )
            except manifest_module.ManifestError as error:
                print(f"relkit audit: {error}", file=sys.stderr)
                return 2
            failures.extend(str(problem) for problem in problems)
            failures.extend(
                f"overlay mount not owned by this repository: {item}" for item in skipped
            )
    elif require_overlay:
        failures.append(f"private overlay manifest is unavailable: {policy.manifest_path}")

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
    suffix = " (including history)" if history else ""
    if policy is not None:
        suffix += " (owner policy enforced)"
    print("relkit audit: passed" + suffix)
    return 0
