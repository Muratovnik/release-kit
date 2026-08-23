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
    # A mounted surface is a private surface, so the two lists are the same list. Where
    # the project declares an overlay, the manifest supplies it and nobody types it
    # twice - a second copy would be the next thing to disagree.
    private_paths = list(settings.exposure.private_paths)
    manifest_path = settings.overlay.manifest_path(root)
    if manifest_path is not None:
        try:
            mounts = manifest_module.read(manifest_path)
        except manifest_module.ManifestError as error:
            # Not a warning: an unreadable manifest means the mounted surfaces are
            # silently unguarded, which is the shape of failure this exists to end.
            print(f"relkit exposure: {error}", file=sys.stderr)
            return 2
        private_root = settings.overlay.private_path(root)
        for mount in mounts:
            try:
                derived = mount.link_path(private_root).resolve().relative_to(root)
            except (ValueError, OSError):
                continue
            private_paths.append(derived.as_posix())

    names = settings.exposure.names(root)
    if not names:
        print(
            f"relkit exposure: no names declared in {settings.exposure.names_file}; "
            "structural rules only"
        )
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
        check_links=settings.exposure.check_links,
        include_candidates=settings.exposure.include_candidates,
    )
    if report.excluded:
        print(f"relkit exposure: {len(report.excluded)} path(s) excluded by configuration")
    if report.baselined:
        print(f"relkit exposure: {len(report.baselined)} recorded finding(s) still present:")
        for finding in report.baselined:
            print(f"  {finding}")
    for path in report.unreadable:
        print(f"relkit exposure: could not read {path}", file=sys.stderr)
    failures = report.failures + ([str(f) for f in report.baselined] if arguments.strict else [])
    if failures:
        print("relkit exposure: failed", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    print("relkit exposure: passed")
    return 0


def _overlay(arguments: argparse.Namespace) -> int:
    root = Path(arguments.root).resolve()
    try:
        settings = config_module.load(root, required=False)
    except config_module.ConfigError as error:
        print(f"relkit: {error}", file=sys.stderr)
        return 2
    private_root = settings.overlay.private_path(root)
    manifest_path = settings.overlay.manifest_path(root)
    if private_root is None or manifest_path is None:
        print("relkit overlay: no [overlay] private_root configured; nothing to check")
        return 0
    try:
        mounts = manifest_module.read(manifest_path)
    except manifest_module.ManifestError as error:
        print(f"relkit overlay: {error}", file=sys.stderr)
        return 2
    problems, skipped = verify_module.check(
        mounts, public_root=root, private_root=private_root
    )
    for name in skipped:
        print(f"relkit overlay: {name} links outside this repository; not checked")
    if problems:
        print("relkit overlay: failed", file=sys.stderr)
        for problem in problems:
            print(f"  {problem}", file=sys.stderr)
        return 1
    print(f"relkit overlay: {len(mounts) - len(skipped)} mount(s) verified")
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
    subcommands = parser.add_subparsers(dest="command", required=True)

    exposure = subcommands.add_parser(
        "exposure", help="Fail when tracked files carry material that must not be published."
    )
    exposure.add_argument("--root", default=".", help="Repository to scan (default: .)")
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    arguments = build_parser().parse_args(argv)
    return int(arguments.handler(arguments))


if __name__ == "__main__":
    raise SystemExit(main())
