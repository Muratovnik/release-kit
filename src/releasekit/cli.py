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
import contextlib
import json
import sys
import tempfile
import traceback
from collections.abc import Sequence
from pathlib import Path

from . import __version__, owner, protection, publication, toolchain, update
from . import config as config_module
from .exposure import audit
from .overlay import manifest as manifest_module
from .overlay import verify as verify_module
from .release import changelog as changelog_module
from .release import coordinator
from .result import Result


def _exposure(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    try:
        settings = config_module.load(root, required=False)
    except config_module.ConfigError as error:
        arguments.result.error("configuration_error", error)
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
            arguments.result.error("configuration_error", error)
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
        arguments.result.error("check_error", error)
        print(f"relkit exposure: {error}", file=sys.stderr)
        return 2
    arguments.result.exposure(report)
    if report.excluded:
        print(f"relkit exposure: {len(report.excluded)} path(s) excluded by configuration")
    if report.baselined:
        print(f"relkit exposure: {len(report.baselined)} recorded finding(s) still present:")
        for finding in report.baselined:
            print(f"  {finding}")
    failures = report.failures + ([str(f) for f in report.baselined] if arguments.strict else [])
    if failures:
        for failure in failures:
            arguments.result.error("check_failed", failure)
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
        result=arguments.result,
    )


def _overlay(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    try:
        policy = owner.discover(root)
        mounts = manifest_module.read(policy.manifest_path)
        problems, skipped = verify_module.check(mounts, public_root=root, private_root=policy.root)
    except (owner.OwnerPolicyError, manifest_module.ManifestError, RuntimeError) as error:
        arguments.result.error("check_error", error)
        print(f"relkit overlay: {error}", file=sys.stderr)
        return 2
    arguments.result.data.update(verified_mounts=len(mounts) - len(skipped), skipped=skipped)
    for name in skipped:
        print(f"relkit overlay: {name} links outside this repository; not checked")
    if problems:
        for problem in problems:
            arguments.result.error("check_failed", problem)
        print("relkit overlay: failed", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"relkit overlay: {len(mounts) - len(skipped)} mount(s) verified")
    return 0


def _protect(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    if arguments.action == "check":
        if arguments.dry_run or arguments.plan_hash:
            arguments.result.error(
                "invalid_arguments", "check does not accept installation options"
            )
            return 2
        if problem := protection.problem(root):
            arguments.result.error("check_failed", problem)
            arguments.result.data["guard"] = "invalid"
            print(f"relkit protect: {problem}", file=sys.stderr)
            return 1
        arguments.result.data["guard"] = "valid"
        print("relkit protect: owner pre-push guard is installed")
        return 0
    try:
        plan = protection.install_plan(root)
        arguments.result.data["plan"] = plan
        if arguments.plan_hash and arguments.plan_hash != plan["plan_sha256"]:
            raise protection.ProtectionError("reviewed hook plan is stale; review a new dry run")
        if arguments.dry_run:
            print("relkit protect: dry run; no hook written")
            return 0
        path = protection.install(root, plan_hash=plan["plan_sha256"])
    except protection.ProtectionError as error:
        arguments.result.error("protection_error", error)
        print(f"relkit protect: {error}", file=sys.stderr)
        return 2
    arguments.result.data.update(guard="installed", path=str(path))
    print(f"relkit protect: installed {path}")
    return 0


def _notes(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    path = root / arguments.changelog
    try:
        policy = config_module.load(root, required=False).changelog
    except config_module.ConfigError as error:
        arguments.result.error("configuration_error", error)
        print(f"relkit notes: {root / config_module.CONFIG_NAME}: {error}", file=sys.stderr)
        return 2
    try:
        with path.open(encoding="utf-8", newline="") as source:
            text = source.read()
    except (OSError, UnicodeError) as error:
        arguments.result.error("io_error", error, path=str(path))
        print(f"relkit notes: {path}: {error}", file=sys.stderr)
        return 2
    profile = "strict" if arguments.strict and policy.profile == "legacy" else policy.profile
    try:
        entry = changelog_module.entry_for(
            text, arguments.version, profile=profile, first_version=policy.first_version
        )
    except changelog_module.ChangelogError as error:
        arguments.result.error("invalid_notes", error, path=str(path), line=error.line)
        print(f"relkit notes: {path}:{error.line}: {error}", file=sys.stderr)
        return 1
    if entry is None:
        arguments.result.error(
            "missing_notes", f"no entry for {arguments.version}", path=str(path), line=1
        )
        print(
            f"relkit notes: {path}:1: no entry for {arguments.version}",
            file=sys.stderr,
        )
        return 1
    arguments.result.data.update(version=arguments.version, notes=entry + "\n", output=None)
    if arguments.output:
        output = root / arguments.output
        temporary: Path | None = None
        try:
            for source_path in (path, root / config_module.CONFIG_NAME):
                if output.resolve() == source_path.resolve() or (
                    output.exists() and source_path.exists() and output.samefile(source_path)
                ):
                    raise OSError("output must not replace the changelog or its policy")
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                newline="",
                dir=output.parent,
                prefix=f".{output.name}.",
                suffix=".tmp",
                delete=False,
            ) as destination:
                temporary = Path(destination.name)
                destination.write(entry + "\n")
            temporary.replace(output)
        except OSError as error:
            arguments.result.error("io_error", error, path=str(output))
            print(f"relkit notes: {output}: {error}", file=sys.stderr)
            return 2
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        arguments.result.data["output"] = str(output)
        return 0
    if arguments.json:
        return 0
    try:
        binary_stdout = getattr(sys.stdout, "buffer", None)
        if binary_stdout is not None:
            binary_stdout.write((entry + "\n").encode("utf-8"))
        else:
            sys.stdout.write(entry + "\n")
    except (OSError, UnicodeError) as error:
        arguments.result.error("io_error", error)
        print(f"relkit notes: stdout: {error}", file=sys.stderr)
        return 2
    return 0


class ArgumentError(ValueError):
    pass


class Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise ArgumentError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = Parser(prog="relkit", description=__doc__.splitlines()[0], allow_abbrev=False)
    parser.add_argument("--json", action="store_true", help="Emit one versioned JSON result")
    parser.add_argument(
        "--version",
        dest="show_version",
        action="store_true",
        help="Show release-kit and engine versions",
    )
    subcommands = parser.add_subparsers(dest="command")

    release = subcommands.add_parser(
        "release", help="Plan, run or resume a GitHub tag release; CI publishes."
    )
    release.add_argument("action", choices=("plan", "run", "resume", "status"))
    release.add_argument("version", help="Stable X.Y.Z or vX.Y.Z")
    release.add_argument("--root", default=".", help="Owning repository")
    release.add_argument(
        "--publish",
        action="store_true",
        help="Authorize only the planned tag/branch push and CI publication",
    )
    release.add_argument(
        "--plan-hash", default="", help="Require the exact reviewed plan fingerprint"
    )
    release.add_argument(
        "--no-download",
        action="store_true",
        help="Forbid audit engine downloads (release assets still download)",
    )
    release.set_defaults(
        handler=lambda arguments: coordinator.run(
            Path(arguments.root),
            arguments.action,
            arguments.version,
            publish=arguments.publish,
            plan_hash=arguments.plan_hash,
            no_download=arguments.no_download,
            accept_ci_attempt=arguments.accept_ci_attempt,
            result=arguments.result,
        )
    )
    release.add_argument(
        "--accept-ci-attempt",
        type=int,
        default=0,
        help="Resume only: explicitly accept a reviewed newer attempt of the same CI run",
    )

    updater = subcommands.add_parser(
        "update", help="Review and safely update one project's pinned zipapp."
    )
    updater.add_argument("--root", default=".", help="Owning project repository")
    updater.add_argument(
        "--repository", default="", help="GitHub OWNER/REPO (default: installed build metadata)"
    )
    updater.add_argument(
        "--release", default="", help="Stable release tag (default: latest published release)"
    )
    updater.add_argument(
        "--artifact", type=Path, help="Use a reviewed local zipapp instead of GitHub"
    )
    updater.add_argument("--sha256", default="", help="Required expected SHA-256 for --artifact")
    updater.add_argument("--plan-hash", default="", help="Require the exact reviewed dry-run plan")
    updater.add_argument(
        "--dry-run",
        action="store_true",
        help="Show changes without executing/installing candidate code",
    )
    updater.add_argument(
        "--yes",
        action="store_true",
        help="Confirm reviewed changes, including separately authorized hook writes",
    )
    updater.add_argument(
        "--refresh-guard",
        action="store_true",
        help="Only re-pin reviewed current files after an interrupted/manual integration",
    )
    updater.add_argument(
        "--rollback",
        action="store_true",
        help="Restore the previous artifact/guard from the recorded backup",
    )
    updater.add_argument(
        "--no-download",
        action="store_true",
        help="Forbid engine downloads during the post-update audit",
    )
    updater.set_defaults(
        handler=lambda arguments: update.run(
            Path(arguments.root),
            artifact_path=arguments.artifact,
            sha256=arguments.sha256,
            repository=arguments.repository,
            release=arguments.release,
            dry_run=arguments.dry_run,
            yes=arguments.yes,
            refresh_guard=arguments.refresh_guard,
            plan_hash=arguments.plan_hash,
            rollback=arguments.rollback,
            no_download=arguments.no_download,
            result=arguments.result,
        )
    )

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
    notes.add_argument("--root", default=".", help="Repository policy and base for relative paths")
    notes.add_argument("--changelog", default="CHANGELOG.md")
    notes.add_argument("--output", help="Write to this file instead of standard output")
    notes.add_argument(
        "--strict",
        action="store_true",
        help="Reject duplicate and empty entries; does not weaken a configured Vue-like profile",
    )
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
    protect.add_argument(
        "--dry-run", action="store_true", help="Preview the exact owned hook change"
    )
    protect.add_argument("--plan-hash", default="", help="Require a reviewed installation plan")
    protect.set_defaults(handler=_protect)
    for command in subcommands.choices.values():
        command.allow_abbrev = False
        command.add_argument("--json", action="store_true", default=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    options = argv[: argv.index("--")] if "--" in argv else argv
    json_mode = "--json" in options
    result = Result()
    parser = build_parser()
    try:
        arguments = parser.parse_args(argv)
        if arguments.show_version:
            if json_mode:
                result.data["engines"] = {
                    name: tool.version for name, tool in toolchain.TOOLS.items()
                }
                print(json.dumps(result.envelope(["version"], None, 0)))
            else:
                print(f"release-kit {__version__} ({toolchain.versions()})")
            return 0
        if arguments.command is None:
            parser.error("a command is required")
    except ArgumentError as error:
        if json_mode:
            result.error("invalid_arguments", error)
            print(json.dumps(result.envelope([], None, 2)))
        else:
            parser.print_usage(sys.stderr)
            print(f"relkit: error: {error}", file=sys.stderr)
        return 2
    arguments.result = result
    if not json_mode:
        return int(arguments.handler(arguments))
    command = [arguments.command]
    if hasattr(arguments, "action"):
        command.append(arguments.action)
    # Engines explicitly inherit these streams; their OS-level output must not
    # share stdout with the JSON envelope. Project commands are captured in logs.
    with contextlib.redirect_stdout(sys.stderr):
        try:
            if arguments.command == "update" and not arguments.dry_run and not arguments.yes:
                result.error("confirmation_required", "JSON updates require explicit --yes")
                code = 2
            else:
                code = int(arguments.handler(arguments))
        except KeyboardInterrupt:
            result.error("interrupted", "Command interrupted; inspect state before retrying")
            code = 3
        except Exception as error:  # noqa: BLE001 -- preserve the JSON process boundary on bugs
            result.error("internal_error", error)
            traceback.print_exc(file=sys.stderr)
            code = 2
    print(json.dumps(result.envelope(command, str(Path(arguments.root).absolute()), code)))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
