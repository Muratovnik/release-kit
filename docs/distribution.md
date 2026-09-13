# Building and qualifying a distribution

Users install the [published CLI](../README.md#first-check-in-an-existing-project)
or [built plugin](plugin.md). This page is for maintainers. The source template in
`plugins/release-kit/` is not an installable plugin.

## Identity and build

CLI and plugin releases use one version and one zipapp payload. Before distributing
changed bytes, update `pyproject.toml`, `src/releasekit/__init__.py`, the plugin
manifest, its `pyproject.toml` and its entry in `uv.lock` together. Add the dated
changelog heading comparing the actual published predecessor. Keep older entries.
Even a documentation-only package rebuild needs a new distribution identity;
never replace already-published bytes under the same version/tag.

From a reviewed checkout, after the [full check](../CONTRIBUTING.md#checks):

```text
python tools/build_release.py dist/VERSION
```

This produces `relkit.pyz`, `release-kit-plugin.zip`, both SHA-256 sidecars and
`release.json`. Distribute the complete set together. The builder refuses an
existing nonempty output directory. The plugin bundles the exact standalone CLI.

For CLI-only development, not the joint publication path:

```text
python tools/build_zipapp.py dist/relkit.pyz
python tools/build_zipapp.py dist/relkit.pyz --repository example/release-kit
```

`--repository` records the maintainer's actual trusted OWNER/REPO for project
updates. `tools/build_plugin.py` remains a plugin-only development builder.
Working-tree test builds do not qualify a release source commit.

The ZIP layout fixes file order, timestamps and host attributes. Source/package
versions and the dated changelog must agree. The CLI carries the full MIT license
and maintainer metadata in its existing `releasekit/build.json` entry; the plugin
and source also carry `LICENSE`.

The plugin packages user instructions, CLI/MCP references, license and starter
examples. CONTRIBUTING, this distribution guide and the disclosure procedure stay
in source; their links in packaged Markdown point to the matching `vVERSION` tag,
including section anchors. Do not distribute a test package under an existing tag.

## Integrity and trust

A SHA-256 sidecar detects changed bytes relative to that digest; it is not an
independent identity check when downloaded beside the file. The plugin's
`package.json` is a **hash inventory, not a digital signature**. Review/trust of
the original distribution remains necessary; malicious code can carry matching hashes.

Before candidate code runs, package checks require exactly five ordinary files,
both correct sidecars, and a `release.json` naming every other file once. Missing
or extra entries, partial/empty manifests, duplicate keys and bad hashes fail.
The inventory is compared before and after all package checks; reports retain the
checked hashes. This proves consistency and observed absence of drift, not origin.

GitHub's native `gh release verify TAG --repo OWNER/REPO` and
`gh release verify-asset TAG FILE --repo OWNER/REPO` provide separate
[release](https://cli.github.com/manual/gh_release_verify) and
[asset](https://cli.github.com/manual/gh_release_verify-asset) verification.
The coordinator compares signed membership with the REST asset set. Neither that
nor a scanner pass proves code quality or absence of all private information.

## Scanner platform and cache behavior

The CLI requires Python 3.11+. Full audit also needs pinned native scanner archives.
Both engines have Linux GNU x64/arm64, macOS x64/arm64 and Windows x64 pins.
Only Betterleaks has a Windows arm64 pin; native Windows arm64 full audit is
unavailable. GNU pins do not promise musl/Alpine compatibility. Unsupported keys refuse.

An empty Windows machine type is resolved with
[GetNativeSystemInfo](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-getnativesysteminfo),
not inherited `PROCESSOR_*` variables. Emulation may report a compatible architecture;
there is no assumed x64 fallback.

Scanner archives/executables default to the target's ignored `.cache/release-kit/`.
Managed-cache use verifies the archive digest, compares the executable with it,
then checks its version. Pre-0.4.1 caches need an online audit to retain archives.
`RELKIT_CACHE_DIR` can select a verified shared cache; external writes also require
that exact approved absolute path in `RELKIT_APPROVED_EXTERNAL_CACHE`.
`RELKIT_BETTERLEAKS` / `RELKIT_LYCHEE` instead select operator-provisioned executables:
only their version is checked, so their independent verification belongs to the operator.

`--no-download` refuses missing verified scanner archives. Release/update asset
downloads are separate; the flag is not a general offline promise. See
[updates](updates.md) and [local releases](local-releases.md).

## Source and candidate checks

Development mode runs source checks, one test build and checks that package:

```text
python tools/check_distribution.py
```

For a release, source qualification and actual package qualification are separate:

```text
python tools/check_distribution.py --source-only
python tools/check_distribution.py --assets dist --version X.Y.Z
```

Package mode never rebuilds or runs source tests. It validates the complete set,
runs CLI and real-scanner onboarding checks, and launches the extracted plugin
through its own `.mcp.json`, including cwd, env and timeouts. Launcher failure
reports the exit code and a bounded stderr tail (stdout when stderr is empty).
The onboarding scenario includes a never-issued synthetic token: Betterleaks must
return its findings exit while Lychee passes, then both must pass after removing
that fixture. Operational errors do not count as detection. Broken-link and index
checks remain separate. A mocked helper test does not qualify the real scanners.
An SDK test does not establish desktop discovery.

The coordinator supplies a Git-free committed snapshot with separate assets:

```text
python tools/check_distribution.py --assets ABSOLUTE_ASSETS --version X.Y.Z --work-dir ABSOLUTE_SCRATCH
```

The explicit parent must already exist and belong to this operation. A snapshot
never discovers an ancestor's checkout or invents a source SHA: the coordinator
receipt binds its source and candidate. A standalone package check proves the
behavior of the supplied bytes, not their source origin.

## Check state and reports

A checkout defaults to ignored `.cache/release-kit-checks/`. With `--work-dir`,
state lives under `REVIEWED_PARENT/release-kit-checks/`. Reuse is scoped to that
selected parent; independent coordinator scratch parents do not share an implicit
external cache. No ancestor path or user-wide environment is adopted automatically.

That state holds one uv cache, one SDK environment per interpreter cache tag, and
compact `reports/run-ID.json` files. `uv run --locked --exact` owns synchronization
against the existing plugin lock; see [uv synchronization](https://docs.astral.sh/uv/concepts/projects/sync/).
Changing the lock does not require a bespoke environment migration. Runs sharing
state serialize through `check.lock`; a busy lock refuses without modifying it.
A dead recorded PID is not proof that all descendants stopped. After an abrupt
host kill, preserve the lock and diagnostics until the owned workers are reconciled,
or select a new explicitly owned parent. No automatic stale-lock deletion occurs.
A directory owned by another tool is not adopted.

Every run has fresh disposable fixtures. A package test still provisions a new
installed-plugin runtime; the reusable SDK is the test client's environment, not
a substitute for testing first startup. On success the newly allocated fixture
roots are removed, while reusable state and the compact report remain. Unknown
run-root entries, leftover process scratch, aliases or a failed check retain the
run for inspection. Cleanup does not touch input candidate files, previous runs,
other tools' directories or user installations. Trusted fixture code runs normally;
this is not a sandbox against hostile concurrent writers inside a fixture.

The report distinguishes check outcome from cleanup and identifies the host and
artifact hashes. Its compact summary also appears as `distribution-check: result`
in the job log, so it is available after an ephemeral runner disappears. Whole
runtime directories and captured private command output are not uploaded as evidence.
Review retained failure directories before removing them.

## Command lifetime and reuse decisions

The coordinator and distribution stages use the same bounded command runner.
Commands receive separate arguments and noninteractive stdin. Each source suite
has a 3600-second limit; build and package stages retain a 1800-second limit.
The full base suite can exceed thirty minutes on Windows with owned process
startup and teardown. This project's coordinator allows 7200 seconds for its
combined base/MCP command; other repositories retain their configured limit.
POSIX execution owns a process group. Cooperative release-kit runners handle SIGTERM,
stop their nested workers and unwind before releasing shared state; a short grace
period precedes the final group kill. Windows uses a native kill-on-close Job Object
and a startup barrier so the command cannot spawn outside the job before assignment.
Windows cleanup waits for process teardown as well as empty job accounting before
returning. The asynchronous MCP executor also uses this Windows Job adapter and
waits for its cleanup without blocking the event loop.

Timeouts remain failures/unknown remote outcomes, not proof a publication did not
happen. Ordinary owned descendants are stopped on timeout, interruption and normal
exit. An unconfirmed cleanup keeps the distribution or release state lock, receipt,
log and scratch. `prepare`, `run` and `resume` return a structured
`release_cleanup_unconfirmed` error and no automatic retry suggestion. Confirm
that all owned commands and descendants have stopped before removing that exact
lock; preserve receipts and use the same version to reconcile remote outcomes.
Forced host kills,
power loss and POSIX descendants deliberately leaving their group cannot promise
cooperative reporting or cleanup; retained state requires explicit recovery, not a
PID-only unlock. No global PID search or termination of unrelated processes occurs.
Native Windows/macOS execution still needs platform-specific acceptance evidence.

This reuses [Python subprocess/process groups](https://docs.python.org/3/library/subprocess.html)
and [Windows Job Objects](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects),
not a scheduler. The synchronous runner owns only command lifetime, not environments,
reports or product policy. MCP transport and structured output remain with the SDK.
`uv` continues to resolve/install/synchronize dependencies and `unittest` owns tests.
Nox or an additional lock library would currently add a parallel lifecycle without
removing candidate validation; neither is introduced. The required exact package
membership and source/candidate relationship remain release-kit rules.

## Releasing this repository

After committing synchronized versions and curated notes, with `PYTHONPATH` set to
this checkout's `src`:

```text
python -m releasekit.cli release prepare vX.Y.Z
python -m releasekit.cli release plan vX.Y.Z
python -m releasekit.cli release run vX.Y.Z --publish --plan-hash REVIEWED
```

The configured check is source-only. The coordinator builds from the committed
snapshot, then runs package checks on the actual candidate before creating a tag.
It compares candidate hashes before/after smoke and publishes those prepared bytes,
not a rebuild. GitHub delivery permissions are in [local releases](local-releases.md#optional-github-delivery).

The manual release workflow runs a source matrix, one candidate build and one
package matrix. The separate check workflow retains development-mode checking.
Neither runs on pushes/PRs/tags nor publishes. No hosted runner or visibility change
is required for local preparation. A declared OS matrix is not execution evidence;
record actual runs and complete [publication review](publication-review.md) separately.
