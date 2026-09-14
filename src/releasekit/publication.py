"""The one publication command adopted by repositories."""

from __future__ import annotations

import sys
from pathlib import Path

from . import config as config_module
from . import engines, owner, protection, toolchain
from .exposure import audit
from .overlay import manifest as manifest_module
from .overlay import verify as verify_module
from .result import Result


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
    result: Result | None = None,
) -> int:
    result = result or Result()
    result.data.update(scope="history" if history else "staged" if staged else "worktree")
    result.data["engines"] = {"betterleaks": None, "lychee": None}
    if history:
        try:
            changes = audit.worktree_changes(root)
        except RuntimeError as error:
            result.error("check_error", error)
            print(f"relkit audit: {error}", file=sys.stderr)
            return 2
        # An untracked file is scanned by the worktree pass and cannot reach a
        # remote; only a tracked difference makes the audited tree disagree with the
        # commits under audit. Refusing every push over an unrelated scratch file is
        # how an owner learns to pass --no-verify, which costs the whole gate.
        tracked = sorted(item for item in changes if not item.startswith("??"))
        result.data["untracked_present"] = len(changes) - len(tracked)
        if tracked:
            listed = ", ".join(tracked[:5]) + (", ..." if len(tracked) > 5 else "")
            message = (
                f"history audit requires a clean tracked tree ({len(tracked)} change(s): "
                f"{listed}); commit or set them aside, or use --staged before committing"
            )
            result.error("check_failed", message)
            print(f"relkit audit: {message}", file=sys.stderr)
            return 1
    try:
        settings = config_module.load(root, staged=staged)
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
        result.error("configuration_error", error)
        print(f"relkit audit: {error}", file=sys.stderr)
        return 2

    try:
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
    except RuntimeError as error:
        result.error("check_error", error)
        print(f"relkit audit: {error}", file=sys.stderr)
        return 2
    result.exposure(report)
    failures = list(report.failures)
    if policy is not None and (guard_problem := protection.problem(root)):
        failures.append(guard_problem)
    if strict:
        failures.extend(str(finding) for finding in report.baselined)
    if history:
        try:
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
                    forbid_png_metadata=settings.exposure.forbid_png_metadata,
                )
            )
        except RuntimeError as error:
            result.error("check_error", error)
            print(f"relkit audit: {error}", file=sys.stderr)
            return 2

    if policy is not None and policy.manifest_path.is_file():
        if policy.root.is_dir():
            try:
                mounts = manifest_module.read(policy.manifest_path)
                problems, skipped = verify_module.check(
                    mounts, public_root=root, private_root=policy.root
                )
            except (manifest_module.ManifestError, RuntimeError) as error:
                result.error("check_error", error)
                print(f"relkit audit: {error}", file=sys.stderr)
                return 2
            failures.extend(str(problem) for problem in problems)
            failures.extend(
                f"overlay mount not owned by this repository: {item}" for item in skipped
            )
    elif require_overlay:
        failures.append(f"private overlay manifest is unavailable: {policy.manifest_path}")

    if report.baselined:
        print(f"relkit audit: {len(report.baselined)} baselined finding(s) remain")

    engine_failures: list[str] = []
    engine_results = result.data["engines"] = {"betterleaks": None, "lychee": None}
    # Provision the engines before either scan starts. Each verifies its own archive
    # and executable, and doing that one after the other put the whole of the second
    # verification on the clock for no reason: they share nothing.
    wanted = [
        name
        for name, enabled in (
            ("betterleaks", settings.exposure.check_secrets),
            ("lychee", settings.exposure.check_links),
        )
        if enabled
    ]
    try:
        if wanted:
            toolchain.prepare(wanted, root=root, allow_download=allow_download)
        if settings.exposure.check_secrets:
            engine_results["betterleaks"] = engines.betterleaks(
                root,
                config=settings.exposure.betterleaks_config,
                history=history,
                staged=staged,
                include_candidates=settings.exposure.include_candidates and not staged,
                allow_download=allow_download,
            )
            if engine_results["betterleaks"]:
                engine_failures.append("Betterleaks failed")
        if settings.exposure.check_links:
            engine_results["lychee"] = engines.lychee(
                root,
                staged=staged,
                include_candidates=settings.exposure.include_candidates,
                allow_download=allow_download,
            )
            if engine_results["lychee"]:
                engine_failures.append("Lychee failed")
    except (OSError, RuntimeError) as error:
        result.error("engine_error", error)
        for failure in sorted(set(failures + engine_failures)):
            result.error("check_failed", failure)
        print(f"relkit audit: {error}", file=sys.stderr)
        return 2

    failures.extend(engine_failures)
    if failures:
        print("relkit audit: failed", file=sys.stderr)
        for failure in sorted(dict.fromkeys(failures)):
            result.error("check_failed", failure)
            print(f"  {failure}", file=sys.stderr)
        return 1
    suffix = " (including history)" if history else ""
    if policy is not None:
        suffix += " (owner policy enforced)"
    print("relkit audit: passed" + suffix)
    return 0
