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

`--repository` records the maintainer's actual trusted OWNER/REPO for future
project updates. `tools/build_plugin.py` also remains a plugin-only development
builder. Working-tree test builds do not qualify a release source commit.

The deterministic ZIP layout fixes file order, timestamps and ZIP host attributes.
Source/package versions and the dated changelog must agree. The full MIT license
and maintainer metadata are included in `releasekit/build.json` inside the CLI;
the plugin and source also carry `LICENSE`. The existing metadata entry preserves
compatibility with old project updaters.

## Integrity and trust

A SHA-256 sidecar detects changed bytes relative to that digest; it is not an
independent identity check when obtained from the same publisher as the file.
The plugin's `package.json` is a **hash inventory, not a digital signature**.
The launcher compares packaged files with it and checks component versions.
An initially malicious package can contain a matching inventory: review/trust of
the distribution source remains necessary.

Package checks require exactly the five distribution files before executing any
candidate code. Both sidecars must name and hash their corresponding payloads.
`release.json` must name every other file exactly once with the correct digest;
an empty, partial or duplicate-key manifest cannot select its own coverage.
Missing files, extra entries and non-file members are failures. The same inventory
is compared before and after all package checks; reports retain the checked hashes.
This establishes consistency and absence of observed drift, not publisher identity.

GitHub immutable-release verification is a separate native mechanism. For a
reviewed published version and downloaded assets, use `gh release verify TAG`
and `gh release verify-asset TAG FILE --repo OWNER/REPO` with the intended repository
(the first command also accepts `--repo`). See the official
[release verification](https://cli.github.com/manual/gh_release_verify) and
[asset verification](https://cli.github.com/manual/gh_release_verify-asset) manuals.
The coordinator compares signed publication membership with the REST asset set;
a verified release is still not proof of code quality or absence of secrets.

## Scanner platform and cache behavior

The base CLI requires Python 3.11+. Full audit additionally selects pinned scanner
archives for the actual OS/architecture. Both engines have Linux GNU x64/arm64,
macOS x64/arm64 and Windows x64 assets in the current pin set. Betterleaks alone
has a Windows arm64 pin; native Windows arm64 full audit is therefore unavailable.
GNU Linux pins do not promise musl/Alpine compatibility. Unsupported keys refuse.

If Python reports an empty Windows machine type, release-kit queries
[GetNativeSystemInfo](https://learn.microsoft.com/en-us/windows/win32/api/sysinfoapi/nf-sysinfoapi-getnativesysteminfo),
not inherited `PROCESSOR_*` variables. Under emulation the API may report a
compatible architecture. There is no assumed x64 fallback for unknown platforms.

Default scanner archives/executables live under the target's ignored
`.cache/release-kit/`. The managed-cache path verifies the pinned archive SHA-256,
compares the executable with the archive, and checks its reported version before
execution. Old pre-0.4.1 caches need an online audit to retain verified archives.

`RELKIT_CACHE_DIR` selects a shared cache. Reading a complete verified external
cache is supported; writes also require approval of that exact absolute path in
`RELKIT_APPROVED_EXTERNAL_CACHE`. `RELKIT_BETTERLEAKS` and `RELKIT_LYCHEE` select
operator-provisioned executables: this explicit override checks their reported
version, not the managed-archive digest chain. The operator owns their independent
verification. Do not mistake a version string for executable authenticity.

`--no-download` fails when required verified archives are missing. It does not
mean all release/update operations are offline; their own asset downloads are
separate. See [updates](updates.md) and [local releases](local-releases.md).

## Check a downloaded candidate without rebuilding

The manual candidate workflow uses this mode after downloading the exact artifact
built by its candidate job. The source snapshot and requested version must match:

```text
python tools/check_distribution.py --assets dist --version X.Y.Z
```

This checks the complete set, CLI smoke, real-scanner onboarding and plugin stdio
against those files. It neither rebuilds nor reruns source tests. The plugin starts
with the command, arguments, cwd, environment and timeouts in its own `.mcp.json`,
not a manually repaired launcher invocation. Native desktop discovery is separate.

The coordinator's snapshot has no `.git`, and its assets are outside that snapshot.
It therefore invokes the same mode with absolute asset and existing scratch paths:

```text
python tools/check_distribution.py --assets ABSOLUTE_ASSETS --version X.Y.Z --work-dir ABSOLUTE_SCRATCH
```

The runner creates a new owned child of the explicit scratch parent, never a
working directory inside the candidate. With a normal checkout, omitting
`--work-dir` uses its usual project-local storage. A Git-free snapshot requires the
explicit scratch path and never borrows an ancestor's Git identity. Its report
records no invented source commit; the coordinator's receipt binds the committed
snapshot and candidate. Package hashes alone do not prove the origin of a standalone
input. Used by itself, this mode is package evidence, not source qualification.

## Releasing this repository

The source uses local build/check/smoke with an explicit GitHub delivery adapter.
No paid runner, mandatory hosted build or repository visibility change is needed.
From an environment with `PYTHONPATH` set to this checkout's `src`:

```text
python -m releasekit.cli release prepare vX.Y.Z
python -m releasekit.cli release plan vX.Y.Z
python -m releasekit.cli release run vX.Y.Z --publish --plan-hash REVIEWED
```

Commit the new version and curated notes before preparation. The configured check
uses `check_distribution.py --source-only` for base and MCP source tests. After
building the actual candidate from committed bytes, the configured smoke invokes
`check_distribution.py --assets {assets} --version {version} --work-dir {temp}`.
All package checks therefore cover the exact files whose hashes enter the candidate
receipt before tagging; a throwaway working-tree build cannot qualify those files.
The coordinator also compares its candidate inventory before/after smoke. A failure
prevents preparation from passing. Run publishes the prepared bytes, not a rebuild.
GitHub write/admin-read permissions and immutability prerequisites are documented
in [local releases](local-releases.md#optional-github-delivery).

For development, the runner without flags still runs source tests, builds a
working-tree test distribution and checks it. That is not the publication candidate.
Hosted workflows are manual supplementary platform checks. They publish no release
and do not run on branch/tag pushes. Record actual host, Python version, artifact
digests and outcomes. A declared platform list is not execution evidence. Complete
the separate [publication review](publication-review.md) before changing visibility
or sharing a previously private distribution.
