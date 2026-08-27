"""Two mechanisms, one entry point, no dependency between them.

`relkit exposure` verifies that a repository carries nothing belonging to the machine
it was written on. `relkit notes` reads the release notes a repository already wrote.
Either runs without the other: a project that publishes nothing still wants the first,
and a project with nothing private still wants the second. A release pipeline may run
the exposure gate before it tags, which is the only order that helps - a tag is what
triggers publication, so a check after it reports on something already published.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path

from . import __version__, owner, protection, publication, toolchain
from . import config as config_module
from .exposure import audit
from .overlay import manifest as manifest_module
from .overlay import verify as verify_module
from .release import changelog as changelog_module


def _exposure(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    try:
        settings = config_module.load(root, required=False)
    except config_module.ConfigError as error:
        print(f"relkit: {error}", file=sys.stderr)
        return 2
    private_paths = list(settings.exposure.private_paths)
    providers = {
        name: provider.allowed_surfaces for name, provider in settings.exposure.providers.items()
    }
    policy = None
    if arguments.owner:
        try:
            policy = owner.discover(root)
            owner_rules = policy.load()
            names = owner_rules.private_values
            mounts = (
                manifest_module.read(policy.manifest_path) if policy.manifest_path.is_file() else ()
            )
        except (owner.OwnerPolicyError, manifest_module.ManifestError) as error:
            print(f"relkit exposure: {error}", file=sys.stderr)
            return 2
        for mount in mounts:
            try:
                derived = mount.link_path(policy.root).absolute().relative_to(root)
            except ValueError:
                continue
            private_paths.append(derived.as_posix())
    else:
        names = ()
        owner_rules = owner.OwnerRules()
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
            include_candidates=settings.exposure.include_candidates,
        )
    except RuntimeError as error:
        print(f"relkit exposure: {error}", file=sys.stderr)
        return 2
    if report.excluded:
        print(f"relkit exposure: {len(report.excluded)} path(s) excluded by configuration")
    if report.baselined:
        print(f"relkit exposure: {len(report.baselined)} recorded finding(s) still present:")
        for finding in report.baselined:
            print(f"  {finding}")
    failures = report.failures + ([str(f) for f in report.baselined] if arguments.strict else [])
    if failures:
        print("relkit exposure: failed", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("relkit exposure: passed")
    return 0


def _audit(arguments: argparse.Namespace) -> int:
    return publication.run(
        Path(arguments.root).resolve(),
        history=arguments.history,
        staged=arguments.staged,
        strict=arguments.strict,
        owner_mode=arguments.owner,
        require_overlay=arguments.require_overlay,
        allow_download=not arguments.no_download,
    )


def _overlay(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    try:
        policy = owner.discover(root)
        mounts = manifest_module.read(policy.manifest_path)
        problems, skipped = verify_module.check(mounts, public_root=root, private_root=policy.root)
    except (owner.OwnerPolicyError, manifest_module.ManifestError, RuntimeError) as error:
        print(f"relkit overlay: {error}", file=sys.stderr)
        return 2
    for name in skipped:
        print(f"relkit overlay: {name} links outside this repository; not checked")
    if problems:
        print("relkit overlay: failed", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"relkit overlay: {len(mounts) - len(skipped)} mount(s) verified")
    return 0


def _protect(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    if arguments.action == "check":
        if problem := protection.problem(root):
            print(f"relkit protect: {problem}", file=sys.stderr)
            return 1
        print("relkit protect: owner pre-push guard is installed")
        return 0
    try:
        path = protection.install(root)
    except protection.ProtectionError as error:
        print(f"relkit protect: {error}", file=sys.stderr)
        return 2
    print(f"relkit protect: installed {path}")
    return 0


def _notes(arguments: argparse.Namespace) -> int:
    path = Path(arguments.changelog)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as error:
        print(f"relkit notes: {error}", file=sys.stderr)
        return 2
    entry = changelog_module.entry_for(text, arguments.version)
    if entry is None:
        print(
            f"relkit notes: {path.name} carries no entry for {arguments.version}",
            file=sys.stderr,
        )
        return 1
    if arguments.output:
        Path(arguments.output).write_text(entry + "\n", encoding="utf-8")
        return 0
    print(entry)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="relkit", description=__doc__.splitlines()[0])
    parser.add_argument(
        "--version",
        action="version",
        version=f"release-kit {__version__} ({toolchain.versions()})",
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    publication_audit = subcommands.add_parser(
        "audit",
        help="Run the complete publication gate: policy, secrets, links, history, overlay.",
    )
    publication_audit.add_argument("--root", default=".", help="Repository to audit")
    scope = publication_audit.add_mutually_exclusive_group()
    scope.add_argument(
        "--history",
        action="store_true",
        help="Inspect publishable branches and tags; requires a clean worktree",
    )
    scope.add_argument(
        "--staged", action="store_true", help="Read tracked content from the Git index"
    )
    publication_audit.add_argument(
        "--strict", action="store_true", help="Fail on every baselined policy finding"
    )
    publication_audit.add_argument(
        "--owner",
        action="store_true",
        help="Require the private sibling policy and the managed pre-push guard",
    )
    publication_audit.add_argument(
        "--require-overlay",
        action="store_true",
        help="Also require and verify the private sibling's mount manifest",
    )
    publication_audit.add_argument(
        "--no-download",
        action="store_true",
        help="Require the pinned engines to be present in the release-kit cache",
    )
    publication_audit.set_defaults(handler=_audit)

    exposure = subcommands.add_parser(
        "exposure", help="Fail when tracked files carry material that must not be published."
    )
    exposure.add_argument("--root", default=".", help="Repository to scan (default: .)")
    exposure.add_argument(
        "--owner",
        action="store_true",
        help="Load private values from the sibling owner policy",
    )
    exposure.add_argument(
        "--strict",
        action="store_true",
        help="Also fail on the recorded baseline, to check whether it has been cleared.",
    )
    exposure.set_defaults(handler=_exposure)

    notes = subcommands.add_parser(
        "notes", help="Print the changelog entry for a version, for use as release notes."
    )
    notes.add_argument("version", help="Version or tag, with or without a leading v")
    notes.add_argument("--changelog", default="CHANGELOG.md")
    notes.add_argument("--output", help="Write to this file instead of standard output")
    notes.set_defaults(handler=_notes)

    overlay = subcommands.add_parser(
        "overlay",
        help="Check that the private surfaces linked into this checkout are still right.",
    )
    overlay.add_argument("--root", default=".", help="Public repository to check (default: .)")
    overlay.set_defaults(handler=_overlay)

    protect = subcommands.add_parser(
        "protect", help="Install or verify the owner-side pre-push publication guard."
    )
    protect.add_argument("action", choices=("install", "check"))
    protect.add_argument("--root", default=".", help="Repository to protect (default: .)")
    protect.set_defaults(handler=_protect)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
