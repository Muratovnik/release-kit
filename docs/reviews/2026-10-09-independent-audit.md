# Independent repository audit — 2026-10-09

## Scope and acceptance

The reviewed baseline is commit `0b3f05fe205efeb4151b581df8f4040d14b6d544`
(release-kit 0.31.0). The review covers the CLI, optional MCP adapter, release
selection and recovery, publication audits, updater and guard boundaries, build
inputs, distributed documentation, and the source/package verification pipeline.
The correction targets release-kit 0.32.0.

Review combined independent subsystem inspection, actual Git and child-process
fixtures, before/after regressions, pinned native-engine runs, and cross-review of
the integrated changes. Existing tests were evidence to extend, rather than proof
that an untested boundary was correct. The original base gate passed with 632
discovered tests and three platform skips;
the defects below include cases that that suite did not exercise.

This report distinguishes managed-project Semantic Versioning from the stable
version format of release-kit's own Python distribution. A project can release
`1.2.0-rc`, `1.2.0-rc.2`, or `1.2.0-rc.2+build.5`. Release-kit's wheel, plugin,
updater and `set_version.py` retain their existing stable `X.Y.Z` policy. SemVer
identity and precedence are not interchangeable with Python version normalization.

## Correction plan and disposition

| Work unit | Intended outcome | Disposition |
| --- | --- | --- |
| Release identities and ordering | One SemVer parser; explicit previews; deterministic next-preview selection; release-channel checks | Implemented with parser, lifecycle and adapter controls |
| Changelog contract and generation | Localized section names; exact commit range; safe draft/export; shipped working template | Implemented and exercised through the built CLI and pinned git-cliff |
| Publication boundaries | Preserve index/history bytes and paths; keep owner privacy mandatory; report scanner failures accurately | Implemented with real Git, archive, filter and native scanner controls |
| Process and recovery ownership | Stop nested workers before releasing state; retain evidence when termination is uncertain | Implemented with real timeout/cancellation and unconfirmed-cleanup controls |
| Reproducible packaging and version updates | Reject inputs that differ from the declared commit; restore or retain a failed version transaction | Implemented with real Git inputs and resolver-worker controls |
| Verification and delivery | Reject empty/invalid test verdicts; qualify exact artifacts; preserve an explicit platform evidence boundary | Integrated into the existing source and distribution gates |

## Findings corrected

Severity describes the consequence of the original behavior: **high** includes
publication/privacy bypass, unintended execution or deletion, source overwrite,
and loss of trustworthy recovery ownership; **medium** includes incorrect release
selection, false qualification, broken recovery inspection, and unusable output.

### Release identities and changelog

| ID | Severity | Original defect | Correction and regression boundary |
| --- | --- | --- | --- |
| R1 | Capability expansion | The documented stable-only contract rejected release candidates throughout release selection, preparation and publication. | Shared strict SemVer parsing retains build identity and applies SemVer precedence. `release next --bump minor --prerelease rc` selects `rc.N` from published history. Bare `-rc` and explicit full SemVer are supported; malformed numeric identifiers and non-ASCII digits are refused. |
| R2 | Capability expansion | Stable-only predecessor selection did not support preview channels and cumulative final releases. | GitHub and directory adapters verify the exact version, tag and prerelease channel. The stable final release compares with the preceding published stable version; previews use the preceding published preview of the same core, or the stable predecessor. Occupied unpublished tags are reported rather than skipped. |
| R3 | Capability expansion | Changelog validation tied semantic section roles to English headings, making a Russian changelog fail the same release checks. | `[changelog.section_aliases]` maps local headings to semantic roles. English history remains valid. Unknown roles, malformed aliases and redefinitions of built-in English meanings are refused. The CLI, candidate and coordinator use the same mapping. |
| R4 | High | Draft export replaced the original source path with a generated-source label, bypassing the source overwrite guard. | The actual configured changelog and policy remain protected, including hardlink/symlink aliases. A distinct export succeeds without changing source bytes or line endings. |
| R5 | High | git-cliff configuration or ambient prepend settings could write files while a draft was expected on stdout; configured template commands could execute. | The maintained adapter forces stdout, disables template execution, removes ambient prepend behavior and runs through owned-process cleanup. Explicit custom generator commands remain trusted project code. |
| R6 | Medium | The supplied template used an invalid first-release comparison, loosely matched commit types, and omitted some breaking changes and mixed-case Conventional Commit types. | Initial releases use a release-tag link. Exact, case-insensitive type matching preserves `feat`/`fix`/`perf`/`revert` and excludes unrelated names. Breaking changes survive uncommon types, `!`, and either supported breaking footer spelling. |
| R7 | Medium; preview integration | Adding preview releases exposed an implicit most-recent-tag range that omitted changes already included in earlier candidates from final stable notes. | `notes --draft --from-tag TAG` selects an explicit predecessor. The tag must exist, resolve unambiguously to the exact tag ref, and be an ancestor of HEAD. A conflicting short ref cannot silently choose another history. |
| R8 | Medium | Packaged notes documentation linked to a changelog starter that was absent from the plugin archive. | Both starter files are packaged and included in the inventory; the real ZIP test resolves the documentation's relative link. |
| R9 | Medium | Recovery status could not read its own retained-process-cleanup state; a case variant of the reserved candidate receipt name could collide on case-insensitive filesystems. | Receipt validation accepts the documented retained state, and candidate asset checks reserve the receipt name case-insensitively. |

Implementation: [SemVer](../../src/releasekit/semver.py),
[release selection](../../src/releasekit/release/versions.py),
[changelog parser](../../src/releasekit/release/changelog.py),
[generator](../../src/releasekit/generation.py), [CLI](../../src/releasekit/cli.py),
[MCP model](../../src/releasekit_mcp/models.py) and
[MCP routing](../../src/releasekit_mcp/bridge.py).
Discriminating controls include `test_semver`, `test_release_prerelease`,
`test_changelog_generation`, `test_draft_export`, `test_plugin_build`, and
`tools/smoke_changelog.py`.

### Publication, privacy and scanner behavior

| ID | Severity | Original defect | Correction and regression boundary |
| --- | --- | --- | --- |
| P1 | High | Quoted, newline-delimited Git history paths lost deleted non-ASCII paths and altered unusual names. Text-mode current-tree inventories also converted carriage returns into newlines and could omit a real file. | NUL-delimited raw inventories preserve non-ASCII text, distinct carriage-return/newline paths, leading whitespace and deleted historical paths, including current index modes, sparse flags and worktree status. |
| P2 | High | Public refs targeting trees or blobs bypassed parts of path and mode inspection; duplicate blobs could lose additional names. | Exact tree inventories cover the configured public ref families, including non-tag refs and repeated blobs with different paths or modes. |
| P3 | High | Exclusions and baselines could suppress the built-in private owner-policy filenames, including nested archives. | Built-in private filenames remain mandatory findings while configurable structural adoption exceptions retain their existing behavior. |
| P4 | High | Unsafe archive member names stopped inspection before compressed owner files and private values were examined. | Bounded privacy inspection continues in memory for unsafe member names, without extracting any member. Worktree, index, history, baseline and exclusion controls cover the boundary. |
| P5 | Medium | A recognized older guard returned success before execute-permission and effective-dispatcher checks. | Compatible old guards undergo the same liveness checks as the current template. |
| P6 | High | New workspace allocation deleted old temporary directories solely because of age, including modified recovery data and unrelated residue. | Age-only pruning is removed. Cleanup remains limited to recorded ownership and identity; allocating a new run preserves older and unknown data. |
| P7 | Medium | A dangling overlay symlink passed verification when the missing target remained in the private index. | Missing mount targets fail verification even when still indexed. |
| P8 | High | `git checkout-index` executed smudge filters and changed the indexed bytes being passed to scanners. | Materialization reads raw Git blobs. A real filter control confirms no command execution and exact index bytes. |
| P9 | High | A replacement directory symlink allowed old tracked descendants to be read through an external target; sparse index paths could be lost during correction. | Worktree traversal avoids replaced directory links, retains index-owned sparse paths, and refuses conflicting materialization. Directory reparse points are refused before child reads. |
| P10 | Medium | Any nonzero scanner code became a finding, including malformed configuration and operational failure. | Betterleaks findings use native code 10 and Lychee findings code 2; other nonzero statuses are CLI errors (2). Native codes remain visible in JSON. |
| P11 | Medium | Betterleaks created a tokenizer cache inside its scan source and retained it after successful runs. | Source and runtime are separately owned. A native clean fixture scans 58 bytes instead of approximately 2.55 MB and leaves no completed-run residue. Failure diagnostics remain available. |
| P12 | High | Scanner timeout killed only the direct process while a worker could continue writing after return. | Scanners use the shared process owner. Lychee reads an owned file list outside source/runtime. Unconfirmed cleanup retains the complete workspace and returns `engine_cleanup_unconfirmed`. |
| P13 | Medium | Lychee's line/comment/glob input grammar could skip Markdown files with line breaks or leading `#`, or expand literal wildcard names to other files. Actual broken links then produced a clean audit. | Relative filenames are escaped for the native input grammar. Ordinary paths retain a scalable file list; line-breaking paths use bounded argv batches under one engine deadline. Native operational errors outrank findings. The real scanner checks exactly one intended link in each of 48 clean/broken, worktree/index filename cases. |

Implementation: [publication audit](../../src/releasekit/exposure/audit.py),
[rules](../../src/releasekit/exposure/rules.py),
[engines](../../src/releasekit/engines.py),
[publication results](../../src/releasekit/publication.py),
[guard validation](../../src/releasekit/protection.py),
[storage](../../src/releasekit/storage.py), and
[overlay verification](../../src/releasekit/overlay/verify.py).
An earlier publication-safety test packet passed 266 tests. After the final Lychee
and carriage-return inventory corrections, the affected audit, engine and
publication suite passed 126 tests in 21.315 seconds. Each packet explicitly
skipped one native Windows junction control on Linux. Cross-review reran the
original non-tag-ref and sparse-index reproducers after correction and independently
verified literal filename selection with the actual pinned Lychee executable.

### Process ownership, packaging and development gates

| ID | Severity | Original defect | Correction and regression boundary |
| --- | --- | --- | --- |
| D1 | High | MCP cancellation and immediate termination of a nested synchronous relay could leave deeper separately owned commands alive. | Both executors relay SIGTERM through nested runners. Real timeout and cancellation controls reach three nested runners and check for writes after return. |
| D2 | High | Disappearance of the immediate group could turn an inner cleanup failure into an apparently confirmed timeout; natural or forced relay exits could hide surviving nested work. | Inherited anonymous lifetime pipes detect surviving managed descendants even after an early ordinary exit. Abnormal cancellation and forced POSIX termination also produce `CleanupError`. MCP retains scratch with `process_cleanup_unconfirmed` and honors validated CLI cleanup refusals; recovery owners keep their state and locks. Normal cooperative cancellation remains a distinct outcome. |
| D3 | High | The exact-commit builder missed removed tracked inputs, untracked/ignored importable modules, and tracked links whose ignored targets changed under a clean HEAD. Ignored README/license inputs could also change wheel bytes without changing HEAD or Git status. | Strict checkout builds verify root identity and all inputs consumed by the maintained builders. Unsupported tracked symlink/gitlink modes are rejected before reading inputs. Fixed package inputs and the declared README must belong to the verified commit. Real Git controls verify refusal before output creation and preserve a valid alternate tracked README; Git-free snapshots retain their separate source-binding contract. |
| D4 | High | A failed version update left some carriers changed; a timed-out resolver worker could overwrite a lock after rollback. | The version transaction preserves original bytes, owns resolver descendants, restores only after confirmed cleanup, and retains an explicit recovery snapshot when cleanup or restoration is uncertain. |
| D5 | Medium | Successful distribution cleanup did not recognize its own wheel-install fixture directory. | Known phase roots include wheel installation and the new changelog smoke. Unknown roots still prevent deletion. |
| D6 | Medium | The parallel test runner reported success for empty or entirely skipped discovery and failed to count unexpected successes as failures. | Empty/all-skipped suites refuse qualification; expected failures and unexpected successes retain unittest semantics for sequential and parallel execution. |
| D7 | High; baseline and lifecycle integration | The source-gate launcher could die before relaying a nested cleanup failure. During integration, the coordinator also needed to preserve the scanner's new explicit cleanup refusal. | The source launcher owns its command lifetime, and the coordinator preserves the audit's explicit unconfirmed result. Actual integration controls verify that both release and distribution locks remain held. |
| D8 | High; baseline and lifecycle integration | The updater could roll back and unlock after a candidate-audit timeout while a worker still ran. Guard refresh also needed to propagate the new scanner cleanup result. | Candidate commands use owned process execution. Unconfirmed cleanup preserves the pending transaction, original backup, workspace and lock instead of starting automatic rollback or suggesting an immediate retry. |
| D9 | Medium; lifecycle integration | The installed projection launcher and bounded package smoke wrappers closed inherited lifetime descriptors; wheel and secret-fixture cleanup could then run after unresolved descendant cleanup. | The interactive launcher preserves descriptor ownership without changing inherited stdin or Ctrl+C behavior. Bounded smoke commands use the shared runner. Unconfirmed cleanup retains their installation/input fixtures; completed ordinary failures keep normal cleanup. |
| D10 | High; lifecycle integration | A multiprocessing `spawn` worker lost ancestor lifetime descriptors. A real failed test left a child alive, but the distribution check reported ordinary failure and removed its lock. | Each worker receives a separately transferred, identity-validated writer using multiprocessing's existing descriptor transfer. The parent retains its writer through lazy spawning and pool shutdown, then checks EOF. Actual fork/spawn controls preserve the lock on uncertainty; ordinary timeouts still stop workers and release it. |

Implementation: [synchronous runner](../../src/releasekit/processes.py),
[MCP executor](../../src/releasekit_mcp/process.py),
[installed launcher](../../src/releasekit/launcher.py),
[updater](../../src/releasekit/update.py),
[release builder](../../tools/build_release.py),
[version transaction](../../tools/set_version.py),
[distribution check](../../tools/check_distribution.py), and
[parallel test runner](../../tools/parallel_tests.py).

## Coverage and preserved architecture

| Surface | Evidence and disposition |
| --- | --- |
| Release plans, candidate hashes, publish/resume/verify, GitHub and directory adapters | Static review, real local Git/directory lifecycle, mocked external-service boundaries, lost-response recovery and channel checks; corrected identities and recovery validation |
| Policy parsing, current tree/index/history, archives, refs, guards and overlays | Real path/filter/archive fixtures and complete affected suites; corrected boundaries listed above |
| Owner discovery, updater install/rollback, pin verification and archive extraction | Static transaction and path review, regression suites, actual pinned engine provisioning; updater recovery corrected for unconfirmed cleanup; owner discovery and toolchain sources unchanged |
| CLI parsing, JSON status, notes export and MCP request routing | Actual CLI calls, closed-model tests, SDK protocol tests and exact argv controls; corrected SemVer routing, failure classification and retained scratch |
| Wheel, zipapp and plugin contents, sidecars and receipts | Real builds, exact input refusal, archive membership/link tests, clean installation and packaged entrypoint checks |
| Workflows and publication authority | Manual workflow configuration and candidate identity review; no automatic trigger was enabled and no release/tag was published during the audit |
| Documentation and compatibility | Generated template and Russian alias examples checked against runtime behavior; stable own-distribution policy and old-executor sync requirement stated explicitly |

The CLI remains dependency-free. Maintained pinned engines continue to own secret,
link and changelog generation behavior; project policy remains in release-kit.
The MCP adapter continues to use the pinned SDK. No new scheduler, global process
registry, shell execution layer, background cleanup sweep, or release publication
path was introduced.

The maintained checkout already contained all 29 required metadata, document and
plugin input paths. The added strict-build controls close alternative-input
bypasses; they do not identify a missing payload in the original maintained layout.

## Validation record

Aggregate qualification of the final committed 0.32.0 candidate is pending at this
report revision. The completed subsystem controls above do not substitute for that
gate. The final validation record will identify its source revision and results.
Package qualification checks the same seven artifact files before and after
execution; a passing source test is not substituted for artifact evidence.

Native tool controls use Betterleaks 1.8.1, Lychee 0.24.2 and git-cliff 2.14.1.
The actual scanner matrix distinguishes malformed configuration (CLI 2), completed
findings (CLI 1) and clean completion (CLI 0). The packaged changelog smoke checks
initial stable and preview notes, preview progression, stable notes across earlier
previews, exact type matching, breaking changes, translated headings, refusal of
source overwrite, and suppression of template output side effects. It verifies
that HEAD and tags remain unchanged. Separate real-Git controls exercise ambiguous
tag boundaries.

Native execution evidence covers Linux x86_64 and Python 3.12.14. Windows Job
Objects/junctions and native macOS execution need their existing platform gates;
a mocked platform boundary or a skipped conditional test is not native acceptance.
No real hosted release, installed-user update, user hook, production rollback or
client registration was performed. Those operations cannot be inferred from the
local and SDK fixtures.

Multiprocessing `fork` and `spawn` were exercised with real workers, including
constructor, initializer, map and shutdown failures. Native `forkserver` could not
start: this host raises `PermissionError: [Errno 1] Operation not permitted` while
creating an `AF_UNIX` socket, before choosing or binding a filesystem path. Its
code path was reviewed, but that result is not a native forkserver pass.

The version was synchronized with `tools/set_version.py 0.32.0` and uv 0.12.23.
Review of the regenerated lock confirmed the same dependency package set, versions,
artifact URLs and hashes. In addition to the plugin runtime's version, uv changed
lock revision 1 to 5, added 407 upload timestamps and removed four dependency
markers excluding Python 3.12/3.13 on Emscripten. The latter changes affect that
unsupported platform; the lock graph for the supported Linux, Windows and macOS
targets is unchanged. This comparison is separate from native installation proof.
The lock was generated by uv, without manual editing.

POSIX process groups are a cooperative ownership boundary for trusted commands.
Deliberately detached children, unmanaged wrappers that close inherited descriptors,
concealed cleanup failures, uncatchable host kills and power loss do not provide a
portable proof of descendant termination. A forced
or abnormal cancellation is retained for explicit recovery rather than reported as
a clean timeout. See [distribution lifecycle](../distribution.md) for that contract.

## Reference criteria

- [Semantic Versioning 2.0.0](https://semver.org/spec/v2.0.0.html): identifier syntax,
  preview precedence and build metadata.
- [Conventional Commits 1.0.0](https://www.conventionalcommits.org/en/v1.0.0/): exact
  types, case handling and breaking-change markers.
- [Python version specifiers](https://packaging.python.org/en/latest/specifications/version-specifiers/):
  the separate Python distribution version model.
- [GitHub release creation](https://cli.github.com/manual/gh_release_create):
  prerelease channel selection.
- [Keep a Changelog in Russian](https://keepachangelog.com/ru/1.1.0/): translated
  reader-facing section headings.
- Repository-specific requirements in [AGENTS.md](../../AGENTS.md), including
  generated release notes, exact artifacts, ownership-preserving cleanup, and
  separate source, package and native-client acceptance.

Assay review criteria were read from main revision
`a9a7b746c382be1f57cd960be1635ec6b005fc85` and checked against refreshed main
`bffd9ccefa0d41683bf0c7dc2a9ed645cb9ad1b7`. The applied skill instructions and
reference criteria were unchanged; the intervening changes concern evaluation
fixtures and packaging. Review coverage and before/after outcomes,
rather than the presence of method files or passing assertions alone, determined
the disposition of each finding.
