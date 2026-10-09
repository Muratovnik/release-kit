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

## Findings and corrections

Severity describes the consequence of the original behavior: **high** includes
publication/privacy bypass, unintended execution or deletion, source overwrite,
and loss of trustworthy recovery ownership; **medium** includes incorrect release
selection, false qualification, broken recovery inspection, and unusable output;
**low** includes presentation ambiguity. R1–R3 are requested capability expansions
beyond the baseline contract. Other rows identify baseline defects or explicitly
label corrections needed while integrating the expanded lifecycle. The report
records 37 findings and capability changes. D12 records a false refusal introduced
while integrating the expanded process-ownership checks, rather than a defect
attributed to the original baseline.

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
| R10 | Medium; documentation | The Node/Angular example implied that stock generator output was a stdout draft compatible with the bounded changelog profile, without establishing either output or layout compatibility. | The [Node generator documentation](../notes.md#generators) now declares a project-owned script with exact argv, requires the script or project metadata to select the version and range, and explains the stdout option and stock Angular heading/section differences. The writer must satisfy the selected profile; no version or range is injected into a custom command. This correction was checked against source and documentation only. No Node execution is claimed. |
| R11 | Medium | Failed changelog generation exposed only the final output line, discarding an actionable cause when a generic backtrace hint followed it. | The displayed diagnostic retains at most 4096 Unicode characters of normalized stderr, falling back to stdout when stderr has no text. Longer diagnostics preserve their head and tail with an explicit omission marker. Real failing-command and CLI controls retain the cause, final hint and native exit status; network, success-output and cleanup behavior are unchanged. |

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
| D11 | Low; development status | The development smoke success message described checked CLI bytes as published even when qualifying an unpublished candidate. | The success status no longer asserts publication. Validation and exit behavior are unchanged; the actual CLI smoke against the qualified candidate and scoped Ruff checks passed for this wording-only correction. |
| D12 | Medium; lifecycle integration | The MCP executor treated a delayed asynchronous exit notification as evidence that fallback SIGKILL had terminated a live process. A cooperatively exited relay could therefore produce a false `CleanupError`. | POSIX teardown awaits the actual exit notification after signalling the owned group; the PID fallback remains only for a Windows child that may not have joined its job. The accepted exit-status set and production grace are unchanged. A real-child notification-delay regression preserves the distinction between cooperative exit and abnormal cancellation, alongside the existing SIGKILL and surviving-writer controls. |
| D13 | Medium; development test fixtures | Ownership tests could expire their short operation or cancellation budget before the real child wrote its PID receipt. The missing receipt then failed the test before its ownership oracle could run. | The affected sleeper, nested-runner and stdio-request fixtures establish complete PID readiness before the tested timeout or cancellation. Readiness has its own bounded assertion, PID receipts are published atomically, and the existing 0.7-second/two-second operation limits and post-return ownership checks remain in force. |

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

### Earlier candidate identity and qualification status

The earlier qualified clean remote 0.32.0 candidate is source commit
`1c139f6e4c3f77592984c75ef8668da6ebc1fcd9`, with Git tree
`0e385a9b6d8eb83cff48e79dbe724d0f1e012dd5`. The complete canonical
`working-tree-distribution-check`, report `run-k_cqlusk`, passed all eight phases
with exit code 0. Its source identity is a clean checkout of that commit.

| Check | Recorded result | Exact evidence |
| --- | --- | --- |
| Base source suite | Passed | 755 discovered tests, four explicit skips, 56.353 seconds; Linux x86_64, Python 3.12.14 |
| Ruff | Passed | Check and format checks passed; 108 files checked for formatting |
| MCP source/SDK suite | Passed | 75 discovered tests, three explicit skips, 522.697 seconds |
| Strict release build | Qualified | Seven-file inventory below, built from the candidate commit and matched to the gate inventory |
| Package checks on the identified seven files | Passed | `cli-smoke`, `onboarding`, `wheel-install`, `changelog`, and `plugin-stdio`: each passed with exit code 0 |
| Inventory after package execution | Unchanged | Every retained artifact hash matches the gate's before/after inventory |
| Complete canonical distribution gate | Passed | `base`, `mcp`, `build`, and the five package phases above: eight passed, each exit code 0; report `run-k_cqlusk` |
| Separate provided-assets control | Passed | Actual `provided-assets-check`, report `run-syjfbktl`: the same five package phases passed with exit code 0 on the same seven hashes; source and artifacts remained unchanged |

No aggregate wall-clock duration was recorded. The individual source-suite times
above must not be read as the duration of the full distribution gate. The separate
provided-assets run executed the actual package checks with observational wrappers
around identity capture and cleanup; those wrappers called the original operations
and did not replace any check or command. Its cleanup observations are recorded
below.

A subsequent report/status revision, clean source commit
`30dccaeefa8499f6e0885c2356e49337120739a3`, Git tree
`9fd6e1430228d66adc86fdc70a4cf753f31cfef4`, included the report and a one-line
development smoke success-message correction. The actual CLI smoke against the
qualified assets passed with exit code 0 after that wording change; scoped Ruff
check and format checks also passed. A strict rebuild at that revision completed
with exit code 0 and all seven artifact hashes matched the earlier qualified set
below. Actual packaged publication audits at the same revision are recorded later
in this section.

The original eight-phase qualification remains bound to
`1c139f6e4c3f77592984c75ef8668da6ebc1fcd9`; the strict rebuild and publication
audits establish their separate `30dccaeefa8499f6e0885c2356e49337120739a3`
boundary. Both revisions precede the D12, D13 and R11 corrections and cannot stand
in for full qualification of those later changes.

### Qualified artifact identities

These SHA-256 values identify the earlier strict-built and package-qualified
`1c139f6e4c3f77592984c75ef8668da6ebc1fcd9` candidate.
The retained seven-file set matches both actual distribution-check inventories,
and every file was unchanged after package execution.

| File | SHA-256 |
| --- | --- |
| `release-kit-plugin.zip` | `0c4573485e1bb6db9ff9255f6676644715f623c22cb2b0a0058bc2a65366452a` |
| `release-kit-plugin.zip.sha256` | `b3a5fdacdc3a41c81c900041a4df8b5d2ad1ef84f4ac5090247f6478d887bd18` |
| `release.json` | `ff6718379324a0451db09f5a91f7c1b2d7614d571f45e41218a644be3fcec40c` |
| `release_kit-0.32.0-py3-none-any.whl` | `6ef2ff1d2c7b9725a678a8a6333bbf14d6176a6af124860832e902bc133d4b66` |
| `release_kit-0.32.0-py3-none-any.whl.sha256` | `6eda9bccb2abf0e70648809ce0299ded846bcecbba1ed4044706438e1f3ac982` |
| `relkit.pyz` | `5b3554a7fb7af19b15ea001f9e3175a1f211d698f5ffd328ba8b32c075225bb2` |
| `relkit.pyz.sha256` | `ea6549605fa04e02461ea919d884a5a7b91df62b8b57920319df9672cdca198a` |

### Python 3.11 qualification failure, D12 and fixture correction D13

Python 3.11 was initially absent, but normal project-owned provisioning succeeded:
uv 0.12.23 installed native CPython 3.11.17 for Linux x86_64. No global interpreter
installation, credential change or permission override was needed. The provisioned
interpreter then ran the canonical check against clean source commit
`30dccaeefa8499f6e0885c2356e49337120739a3`, version 0.32.0.

Report `run-fac4se0h` records a failed qualification. The base suite passed,
running 755 tests including four explicit skips in 67.903 seconds; Ruff check and
format checks also passed. The MCP suite ran 75 tests in 703.607 seconds, with
three skips and one error in the cancellation branch of
`test_timeout_and_cancellation_stop_deeply_nested_releasekit_commands`. The build
and five package phases were not reached. This is actual native Python 3.11
execution and a failed acceptance result, not an unavailable interpreter or a
successful full check.

A narrow replay of that exact test recorded four false cleanup refusals across
12 stops. In the decisive ready-child cancellation record, the owned group was
already absent, the lifetime pipe reached EOF and the eventual native exit status
was `-SIGINT` (`-2`). The asynchronous process object's `returncode` remained
`None` briefly. Its fallback `kill()` returned without raising, and the executor
marked termination as forced even though the subsequent wait returned the
cooperative status. That callback-ordering observation identifies D12; startup
speed does not explain the ready-child case.

Independent native synchronous controls on the same Python 3.11 interpreter
confirmed the expected distinction. A plain termination handler exited as `-2`,
a developer-style `SystemExit(130)` remained accepted, and three real nested
runners completed with descendant statuses `-15`, `-2` and `-2`, with EOF at every
owned boundary. Separate actual exit-1 and ignored-SIGTERM/real-SIGKILL controls
both retained `CleanupError`. These five controls do not qualify the asynchronous
adapter or justify widening its accepted statuses.

The corrected POSIX path awaits the process's exit notification after its group
has disappeared, within the existing five-second cleanup bound. A PID fallback
remains for an unassigned Windows child. The production grace and accepted
exit-status set are unchanged; actual forced termination, abnormal cancellation
and surviving lifetime writers still require retained recovery.

The permanent regression delays only notification of a real child already reaped
by the watcher. Before correction, it failed for actual exit codes 0 and 130;
its exit-7 control also detected the unnecessary fallback kill. Native group
absence and lifetime EOF are asserted before teardown. These checks isolate the
product correction from fixture startup timing.

After correction, the new notification-delay regression passed on both SDK
interpreters. Six repetitions of the deep-chain method produced 12 ready-child
stops without a cleanup refusal on Python 3.11. The first Python 3.11 lifetime
packet passed those new controls but recorded a separate
error in `test_timeout_kills_only_owned_tree`: the PID receipt was absent after
its existing 0.7-second operation timeout. That packet ran 12 tests with two skips
in 30.073 seconds and is recorded as failed, rather than relabeled a pass on the
strength of its successful D12 controls.

That separate error confirmed D13, a baseline development-fixture defect. The
sleeper timeout/cancellation tests, Windows working-directory control, stdio
request cancellation and added nested-runner control now establish full PID
readiness using real child execution. Atomic receipt publication prevents the
ready marker from exposing incomplete JSON. A bounded startup failure becomes an
assertion rather than the expected operation `TimeoutError`. An explicit successful
readiness postcondition also prevents a cleanup refusal in `execute.finally` from
replacing a startup assertion and accidentally satisfying an expected
`CleanupError`. The tested 0.7-second and two-second operation limits, production
cleanup grace, and checks for a live descendant or writes after return are not
relaxed.

A controlled real sleeper delayed its readiness publication by 1.2 seconds. The
same fixture bytes failed the old pre-readiness test in 0.918 seconds with a missing
PID receipt, then passed the corrected test in 2.177 seconds with the unchanged
0.7-second operation timeout. Both owned PIDs were dead afterward in both cases:
the original failure was in the fixture oracle, not product process termination.
The coherent lifetime family then passed on Python 3.11 and 3.12, running 12 tests
including two Windows-specific skips in 24.561 and 22.532 seconds respectively.
Those packets preceded the final readiness postcondition; a separate actual
never-ready child with an injected cleanup refusal demonstrated that the earlier
helper falsely accepted startup, while the final helper refused it. The product
code did not change during those fixture controls.

The runtime correction and final fixture assertions were published in source
commit `f72ecf115aa4e0a96c9d1a76d828d80366f89dc3`. Release-note and report updates
follow that code commit. Fresh complete qualification on the integrated Python
3.11 and Python 3.12 candidate remains pending. Historical passes and hashes above
remain attached to the source that actually ran them.

### Changelog generation diagnostic and offline control

Actual `notes 0.32.0 --draft --from-tag v0.31.0` generation at source commit
`f72ecf115aa4e0a96c9d1a76d828d80366f89dc3` failed with CLI exit 2. The verified
pinned git-cliff 2.14.1 executable exited 101 while requesting GitHub metadata.
Its full native stderr identified `invalid peer certificate: UnknownIssuer` under
the failed metadata request. Release-kit exposed only the final Rust backtrace
hint and omitted that actionable cause. The native TLS failure is an environment
and upstream execution observation; discarding its diagnostic is the product
defect R11. The exact failing command, native stderr and source identity were
preserved before any retry.

The same actual CLI command succeeded with exit 0 in 1.737 seconds after enabling
the supported per-invocation `GIT_CLIFF_OFFLINE=true` setting. It generated the
entry from local commits and retained the configured static GitHub commit links,
including the full published `f72ecf1` commit identity. Source and tag references
were unchanged. Certificate verification was not disabled or altered. The pinned
executable was already verified in the owned cache; this is not evidence that an
uncached generator can be provisioned without network access.

The [offline recipe](../notes.md#drafting-without-remote-metadata) documents that
boundary and the loss of pull-request titles, labels and other remote enrichment.
The diagnostic correction retains at most 4096 Unicode characters, including an
explicit marker between the retained head and tail when output is longer. This
bounds the displayed diagnostic, not subprocess output collection. Normalized,
nonempty stderr takes precedence; otherwise stdout is used. The same 13-test
packet recorded six assertion failures and no errors before correction, then
passed with no skips in 3.881 seconds on Python 3.12.14. Actual failing child
commands verify the multiline cause and hint, invalid UTF-8 replacement, stderr
precedence and fallback, truncation and silent failure. An actual CLI invocation
preserves native exit 101 as CLI 2 with `generation_error`; separate controls keep
successful output and exact custom argv unchanged. Independent source review
confirmed that network behavior and cleanup routing were untouched.

A separate real failing subprocess replayed the original 853-byte native stderr.
The old adapter's 106-character message omitted the cause and metadata context;
the corrected 842-character message retained both and the final hint, without
terminal color sequences. That replay made no new remote request.

The diagnostic correction and offline documentation were published in source
commit `4fe6f804f3cc11473557fee47af87e88955a295b`. The actual offline notes CLI
then regenerated the 0.32.0 entry from `v0.31.0` with exit 0 in 6.278 seconds.
The generated entry includes full published links for both the MCP and diagnostic
fix commits. Only the generated current entry was replaced; the preamble and
all prior release entries remained byte-for-byte unchanged. This generation
result is not a substitute for final integrated qualification.

### First aggregate failure and fixture correction

The first aggregate attempt, at source commit
`972709b18673b0c8a7ce3cab309fe256ccfbf0ab`, failed with three release-fixture
failures among 755 tests in 76.579 seconds. The failures occurred at Git push or at
a subsequent resume blocked by the retained fixture release lock. They did not
qualify that candidate.

The disposable client repository disabled automatic maintenance, but its bare
server did not. Native traces and inspection of the specific lifetime pipe
identified detached `git maintenance` and its `git gc` child in that server,
still holding the writer after push exited. The EOF refusal therefore reflected
real surviving work. The fixture now disables automatic maintenance in both its
owned repositories, including receive-side automatic maintenance. Product process
cleanup and its immediate uncertainty check were not weakened, and no user
repository maintenance policy was changed.

A narrow reproduction ran the same three fixture methods through two owned parent
runners and nine workers: 108 repetitions produced 108 detached maintenance
launches and two cleanup refusals. Disabling server maintenance produced no such
launches or refusals in 27 repetitions. After the tracked fixture correction, the
three affected release modules passed, running 111 tests through the same parent
and worker arrangement, without instrumentation, in 30.326 seconds. These targeted
results establish the correction's boundary; the candidate qualification table
above records the subsequent aggregate run separately.

### Cleanup observations and forensic limit

Separately, the first failed run retained its distribution lock. The original lock
creation identity and final comparison identity are unavailable, so the exact
reason for that particular retention was not proved. Independent actual-run
controls confirmed that an ordinary failed check removes an unchanged lock and
that changed identities are retained. Those controls establish the decision
boundary, not the missing history of the original run. Its state was preserved,
and subsequent qualification used a fresh, explicitly owned parent.

The separate actual provided-assets run captured the lock and run-directory
identities and observed the real `unlink` and `clean_success` operations removing
those same identities. Both paths were absent immediately after the check's
`main` returned successfully. In a separate execution 19.619 seconds later, both
paths were visible again with different inodes and later change times. That
observation does not establish the responsible actor or mechanism, and the later
observed state was preserved. It must not be substituted for the missing identity
history of the first failed run or attributed to product or host behavior without
further evidence.

An ordinary filesystem negative control made 207 post-deletion observations,
including four independent stat executions, through 99.515 seconds; the deleted
paths remained absent. This
control did not reproduce the later visibility and does not prove its cause. The
qualification results and immediate identity-checked cleanup are recorded above;
the unresolved filesystem history remains an explicit forensic limitation.

### Earlier source publication audits

Strict, no-download publication audits also passed at the earlier source commit
`972709b18673b0c8a7ce3cab309fe256ccfbf0ab`, tree
`5e0d54924a1f183a382949dae35f4f42adb5185a`, on Linux/Python 3.12.14. The current
worktree audit completed in 3.983 seconds and the selected-history audit in 14.474
seconds. Both returned CLI 0 with valid result envelopes, no errors or unconfirmed
cleanup, and native Betterleaks 1.8.1 and Lychee 0.24.2 exit codes of 0.

The effective policy used `.gitleaks.toml`, structural exclusion `tests/*`, no
baseline entries, candidate inspection enabled, and owner mode disabled. History
selection covered HEAD, branches, remotes, tags, and the configured pull-request,
merge-request, change and notes ref families. Policy and Betterleaks inspected
that selected history; Lychee checked the current Markdown snapshot, not every
historical version. The 23 local refs and 20 advertised refs were recorded; source
and refs remained unchanged, with no advertised-ref mismatches afterward. This is
bounded source evidence, not a complete inventory of every hosted surface.

Those earlier source audits do not represent the subsequent qualified commit.
A separate packaged publication audit then checked clean source commit
`30dccaeefa8499f6e0885c2356e49337120739a3`, Git tree
`9fd6e1430228d66adc86fdc70a4cf753f31cfef4`, using the actual qualified
`relkit.pyz` whose SHA-256 is listed above. Strict no-download worktree and
selected-history audits passed in 2.583 and 14.402 seconds respectively on
Linux/Python 3.12.14. Both returned valid CLI-0 envelopes, native Betterleaks and
Lychee status 0, no errors and no unconfirmed cleanup. Source, refs and all seven
artifact hashes remained unchanged.

The recorded scope had 22 advertised refs, 25 local refs and 113 commits reachable
from HEAD and the selected recorded ref objects, including the PR head and merge
refs. There were no advertised-ref mismatches before or after the audits. Policy
and Betterleaks covered selected history; Lychee checked the current Markdown
snapshot. Owner mode was disabled, `tests/*` was a structural exclusion, and no
baseline or download was used. This is concrete packaged publication evidence
for the earlier `30dccaeefa8499f6e0885c2356e49337120739a3` source and old artifact
bytes; final publication checks must identify the later integrated candidate.

### Native tool and platform coverage

Native tool controls use Betterleaks 1.8.1, Lychee 0.24.2 and git-cliff 2.14.1.
The actual scanner matrix distinguishes malformed configuration (CLI 2), completed
findings (CLI 1) and clean completion (CLI 0). The packaged changelog smoke checks
initial stable and preview notes, preview progression, stable notes across earlier
previews, exact type matching, breaking changes, translated headings, refusal of
source overwrite, and suppression of template output side effects. It verifies
that HEAD and tags remain unchanged. Separate real-Git controls exercise ambiguous
tag boundaries.

Native execution evidence includes Linux x86_64 on Python 3.12.14 and the
provisioned Python 3.11.17. The failed 3.11 gate and the historical successful
3.12 gate are distinguished above; final integrated qualification is pending.
Windows Job Objects/junctions and native macOS execution need their existing platform gates;
a mocked platform boundary or a skipped conditional test is not native acceptance.
No real hosted release, installed-user update, user hook, production rollback or
client registration was performed. Those operations cannot be inferred from the
local and SDK fixtures.

The existing [manual release workflow](../../.github/workflows/release.yml) declares
Python 3.11 source checks on Linux, macOS and Windows, one Linux candidate build,
and package checks on the same downloaded candidate across those OS jobs: seven
jobs in total. Its configuration is not evidence that those native runs passed.
Windows/macOS acceptance remains open until actual native runs identify the
reviewed source and checked bytes. Fresh Linux Python 3.11/3.12 acceptance must
identify the integrated D12, D13 and R11 corrections. The dispatched run's
`headSha` must match the final remote candidate commit. The workflow neither publishes nor supplies
native desktop-client discovery proof; that acceptance remains separate from
SDK/stdio startup. Its source stage now discovers an explicit forkserver
regression on supported POSIX interpreters; no new workflow flag is required.

A read-only preflight resolved all four exact upstream action commits in the
workflow and verified each `action.yml` Git blob against its pin. All declare the
`node24` runtime; the pinned
[setup-python README](https://github.com/actions/setup-python/blob/5fda3b95a4ea91299a34e894583c3862153e4b97/README.md)
requires Actions Runner 2.327.1 or later. None declares a Docker-only action
runtime. This verifies external pins and runtime configuration, without claiming
that a workflow or native OS job executed.

The Node/Angular documentation correction in R10 has source/documentation evidence
only. It adds no Node runtime, npm installation or generated-output execution claim
to the native coverage recorded here.

Multiprocessing `fork` and `spawn` were exercised with real workers, including
constructor, initializer, map and shutdown failures. The maintained
`test_forkserver_source_workers_preserve_cleanup_ownership` now explicitly runs
the existing two-worker timeout and cleanup-denial oracles with `forkserver`.
It preserves the separate `spawn` regression and requires distinct worker PIDs
and the selected native start method. An owned short IPC directory avoids an
artificially deep distribution-fixture socket path; only the generated launcher
changes its own `tempfile.tempdir`.

The forkserver test skips an absent start method, Windows, or an actual socket
unsupported/permission/path-limit failure. Worker creation, descriptor transfer
and pool failures remain test failures. On this host native `forkserver` still
cannot start: creating an `AF_UNIX` socket raises
`PermissionError: [Errno 1] Operation not permitted`, before bind. That observed
host refusal is not native forkserver acceptance. The maintained test now makes
the existing manual source jobs exercise the real forkserver boundary on capable
hosts, rather than relying on their default multiprocessing start method.

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
- [git-cliff remote configuration](https://git-cliff.org/docs/configuration/remote/#offline):
  supported offline metadata behavior, also checked against the pinned executable.
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
