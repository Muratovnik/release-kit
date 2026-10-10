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
| Windows plugin runtime paths | Measure the DLL-loader budget in UTF-16 code units for the original path and any alias | Implemented with actual-loader before/after and fixture-padding controls; native qualification of the changed payload remains separate |
| Windows plugin cache compatibility | Check the physical disk-cache budget before initialization while retaining plugin-local ownership | Implemented with physical-path and no-initialization controls on Python 3.11 and 3.12; corrected native installation remains pending |
| Verification and delivery | Reject empty/invalid test verdicts; qualify exact artifacts; preserve an explicit platform evidence boundary | Integrated into the existing source and distribution gates |

## Findings and corrections

Severity describes the consequence of the original behavior: **high** includes
publication/privacy bypass, unintended execution or deletion, source overwrite,
and loss of trustworthy recovery ownership; **medium** includes incorrect release
selection, false qualification, broken recovery inspection, and unusable output;
**low** includes presentation ambiguity. R1–R3 are requested capability expansions
beyond the baseline contract; D14 extends CI coverage of an existing opt-in test.
The remaining 38 entries cover baseline defects, issues exposed while integrating
corrections, and test or documentation problems: 17 high, 20 medium and one low.
Together these are 42 review items: three capability expansions, one coverage
expansion and 38 other issues. Combined baseline/integration rows are labeled
accordingly. D12 records a false refusal introduced while integrating the expanded
process-ownership checks. D13 distinguishes original MCP fixtures from source-gate,
candidate-smoke and launcher fixtures added during this review; they are not
presented as additional product vulnerabilities. D15 records a baseline platform assumption exposed by hosted
macOS execution. D16 groups Windows fixture portability issues and separates
its original 8.3 test and plugin-smoke padding assumptions from tests added
during this review. D17 is a separate baseline product defect in the packaged
Windows launcher's path-length units, not another D16 fixture failure.
D18 records a baseline launcher compatibility and documentation issue exposed by
the upstream installer's cache-path boundary, not an additional security
vulnerability or a failure caused by D17.

### Release identities and changelog

| ID | Severity | Original behavior | Correction and regression boundary |
| --- | --- | --- | --- |
| R1 | Capability expansion | The documented stable-only contract rejected release candidates throughout release selection, preparation and publication. | Shared strict SemVer parsing retains build identity and applies SemVer precedence. `release next --bump minor --prerelease rc` selects `rc.N` from published history. Bare `-rc` and explicit full SemVer are supported; malformed numeric identifiers and non-ASCII digits are refused. |
| R2 | Capability expansion | Stable-only predecessor selection did not support preview channels and cumulative final releases. | GitHub and directory adapters verify the exact version, tag and prerelease channel. The stable final release compares with the preceding published stable version; previews use the preceding published preview of the same core, or the stable predecessor. Occupied unpublished tags are reported rather than skipped. |
| R3 | Capability expansion | The `conventional-changelog` profile tied semantic section roles to English headings, making a Russian changelog fail that profile’s release checks. | `[changelog.section_aliases]` maps local headings to semantic roles. English history remains valid. Unknown roles, malformed aliases and redefinitions of built-in English meanings are refused. The CLI, candidate and coordinator use the same mapping. |
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

| ID | Severity | Original behavior | Correction and regression boundary |
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
An earlier publication-safety suite passed, running 266 tests. After the final Lychee
and carriage-return inventory corrections, the affected audit, engine and
publication suite passed, running 126 tests in 21.315 seconds. Each packet explicitly
skipped one native Windows junction control on Linux. Cross-review reran the
original non-tag-ref and sparse-index reproducers after correction and independently
verified literal filename selection with the actual pinned Lychee executable.

### Process ownership, packaging and development gates

| ID | Severity | Original behavior | Correction and regression boundary |
| --- | --- | --- | --- |
| D1 | High | MCP cancellation and immediate termination of a nested synchronous relay could leave deeper separately owned commands alive. | Both executors relay SIGTERM through nested runners. Real timeout and cancellation controls reach three nested runners and check for writes after return. |
| D2 | High; baseline and lifecycle integration | Disappearance of the immediate group could turn an inner cleanup failure into an apparently confirmed timeout; natural or forced relay exits could hide surviving nested work. | Inherited anonymous lifetime pipes detect surviving managed descendants even after an early ordinary exit. Abnormal cancellation and forced POSIX termination also produce `CleanupError`. MCP retains scratch with `process_cleanup_unconfirmed` and honors validated CLI cleanup refusals; recovery owners keep their state and locks. Normal cooperative cancellation remains a distinct outcome. |
| D3 | High | The exact-commit builder missed removed tracked inputs, untracked/ignored importable modules, and tracked links whose ignored targets changed under a clean HEAD. Ignored README/license inputs could also change wheel bytes without changing HEAD or Git status. | Strict checkout builds verify root identity and all inputs consumed by the maintained builders. Unsupported tracked symlink/gitlink modes are rejected before reading inputs. Fixed package inputs and the declared README must belong to the verified commit. Real Git controls verify refusal before output creation and preserve a valid alternate tracked README; Git-free snapshots retain their separate source-binding contract. |
| D4 | High | A failed version update left some carriers changed; a timed-out resolver worker could overwrite a lock after rollback. | The version transaction preserves original bytes, owns resolver descendants, restores only after confirmed cleanup, and retains an explicit recovery snapshot when cleanup or restoration is uncertain. |
| D5 | Medium | Successful distribution cleanup did not recognize its own wheel-install fixture directory. | Known phase roots include wheel installation and the new changelog smoke. Unknown roots still prevent deletion. |
| D6 | Medium; baseline and integration test gates | The original parallel runner could qualify empty or entirely skipped discovery and failed to count unexpected successes as failures. Later subtest controls exposed method-versus-skip counting that could also reject executed cases or misclassify a real MCP failure. Baseline failure/error serialization also discarded test and subtest identities. | Source and MCP gates record actual successful, expected-failure and successful-subtest callbacks. Entirely skipped suites refuse qualification; real failures and unexpected successes retain failure precedence. Sequential and actual parallel controls cover mixed and entirely skipped cases, and source logs identify each skipped test or subtest with its reason. Failure/error diagnostics also preserve the actual test ID and subtest parameters; counts, verdict priority and ownership behavior are unchanged. |
| D7 | High; baseline and lifecycle integration | The source-gate launcher could die before relaying a nested cleanup failure. During integration, the coordinator also needed to preserve the scanner's new explicit cleanup refusal. | The source launcher owns its command lifetime, and the coordinator preserves the audit's explicit unconfirmed result. Actual integration controls verify that both release and distribution locks remain held. |
| D8 | High; baseline and lifecycle integration | The updater could roll back and unlock after a candidate-audit timeout while a worker still ran. Guard refresh also needed to propagate the new scanner cleanup result. | Candidate commands use owned process execution. Unconfirmed cleanup preserves the pending transaction, original backup, workspace and lock instead of starting automatic rollback or suggesting an immediate retry. |
| D9 | Medium; lifecycle integration | The installed projection launcher and bounded package smoke wrappers closed inherited lifetime descriptors; wheel and secret-fixture cleanup could then run after unresolved descendant cleanup. | The interactive launcher preserves descriptor ownership without changing inherited stdin or Ctrl+C behavior. Bounded smoke commands use the shared runner. Unconfirmed cleanup retains their installation/input fixtures; completed ordinary failures keep normal cleanup. |
| D10 | High; lifecycle integration | A multiprocessing `spawn` worker lost ancestor lifetime descriptors. A real failed test left a child alive, but the distribution check reported ordinary failure and removed its lock. | Each worker receives a separately transferred, identity-validated writer using multiprocessing's existing descriptor transfer. The parent retains its writer through lazy spawning and pool shutdown, then checks EOF. Actual fork/spawn controls preserve the lock on uncertainty; ordinary timeouts still stop workers and release it. |
| D11 | Low; development status | The development smoke success message described checked CLI bytes as published even when qualifying an unpublished candidate. | The success status no longer asserts publication. Validation and exit behavior are unchanged; the actual CLI smoke against the qualified candidate and scoped Ruff checks passed for this wording-only correction. |
| D12 | Medium; lifecycle integration | The MCP executor treated a delayed asynchronous exit notification as evidence that fallback SIGKILL had terminated a live process. A cooperatively exited relay could therefore produce a false `CleanupError`. | POSIX teardown awaits the actual exit notification after signalling the owned group; the PID fallback remains only for a Windows child that may not have joined its job. The accepted exit-status set and production grace are unchanged. A real-child notification-delay regression preserves the distinction between cooperative exit and abnormal cancellation, alongside the existing SIGKILL and surviving-writer controls. |
| D13 | Medium; baseline and integration test fixtures | Ownership tests, including review-added source-gate and candidate-smoke fixtures, started their short operation or cancellation budget before full child PID readiness. A review-added ordinary launcher fixture also imposed a five-second functional-test budget below its supported Git lookup allowance. These fixture assumptions could prevent the intended oracle from running. | Corrected ownership fixtures establish atomic readiness before their operation clock, with bounded startup and a successful-readiness postcondition; their actual timeout, exit-status, late-write and retention assertions remain. Ordinary launcher status/argv/cwd checks share their sibling harness's existing 120-second allowance. Controlled delayed children distinguish the two corrections from product cleanup; original failed-gate startup timing remains unrecorded. The candidate-smoke follow-up below adds a permanent startup delay longer than its unchanged operation timeout and verifies all three real wrappers. |
| D14 | Coverage expansion | The manual Windows source job left the existing real-engine MCP sync acceptance test disabled. A green ordinary matrix therefore did not exercise apply/audit/rollback with `PROCESSOR_*` removed. | The Windows source job provisions all three pinned executable/archive pairs into the exact default cache and enables the existing opt-in. The unchanged test retains its full assertions. Controlled Linux cache/refusal checks passed. Native workflow #5 completed provisioning but failed before MCP; run #6 then executed the named acceptance successfully on Windows. |
| D15 | Medium; baseline platform assumption | A transient POSIX process-group permission result was treated as immediate terminal cleanup failure. Native macOS ordinary timeout controls failed at this boundary; the original baseline already had the same error handling. | Both helpers treat permission denial as present or unconfirmed and retain the existing bounded polling. Only actual ESRCH, together with the existing lifetime and exit-status checks, permits confirmed cleanup. Real-child transient and persistent-denial controls preserve the distinction; hosted run #6 passed the native macOS source controls without weakening cleanup confirmation. |
| D16 | Medium; baseline and review-added test fixtures | Windows source and package verification failed on fixture assumptions about Git/text newline conversion, native symlink spelling, mandatory 8.3 shortening and cleanup of an already absent PID. The original plugin smoke also exceeded the loader budget when no short alias was available. A review-added repeat-cleanup test later assumed that a completed process PID must already be absent. | Fixtures establish declared bytes, compare native path semantics, exercise the actual alias-or-refusal contract and accept a Windows absent-PID result or completion positively confirmed through a signaled process handle. The review-added package history fixture writes its expected LF bytes explicitly; ordinary plugin startup keeps a long, spaced fixture path within the loader budget without requiring an alias. Product byte handling, strict-build checks and launcher behavior are unchanged. Original ownership, history, rollback and refusal oracles remain; baseline and review-added cases are distinguished in the native record. Native run #8 exposed the further completed-PID fixture assumption; its focused follow-up is recorded below. |
| D17 | Medium; baseline product defect | The packaged Windows launcher measured runtime and alias paths with Python character counts instead of UTF-16 code units. Non-BMP names could pass the existing DLL-loader budget while exceeding it in Windows units. | Both loader decisions and the smoke fixture's padding now count UTF-16 units without altering the path. Actual-loader regressions reject the original 291-unit and 297-unit false acceptances, preserve valid aliases and keep the existing threshold and refusal. Focused controls passed on Linux/Python 3.11 and 3.12; native DLL loading and qualification of the changed payload require separate evidence. |
| D18 | Medium; baseline compatibility and documentation | The plugin used an extended-path cache without checking the upstream wheel installer's physical path budget, and documentation claimed that nested caches could exceed the legacy limit. Native Windows installation failed when a cached script and its parent acquired different prefix forms. | The launcher validates the physical disk-cache budget before runtime initialization, retains the existing owned `.runtime/cache`, and asks for a shorter installation path when needed. DLL alias/UTF-16 checks stay separate; this disk-path check adds no new UNC restriction. Focused controls passed on Linux/Python 3.11 and 3.12; corrected native installation remains pending. |

Implementation: [synchronous runner](../../src/releasekit/processes.py),
[MCP executor](../../src/releasekit_mcp/process.py),
[installed launcher](../../src/releasekit/launcher.py),
[packaged plugin launcher](../../plugins/release-kit/scripts/launch.py),
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
| Workflows and publication authority | Manual workflow review and actual hosted source/package execution; earlier source failures and the later Windows package failure remain explicit; no automatic trigger or release/tag publication was introduced |
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

This section records dated source and artifact checkpoints. A pending result
describes the state at that checkpoint, not a live CI status. Subsequent final
candidate source and package results are recorded against their actual checked
bytes in [PR #2](https://github.com/Muratovnik/release-kit/pull/2). The historical
failures and unresolved filesystem attribution below remain part of this record.

### Hosted source qualification at b10a0708 and D16 follow-up

[Native workflow run #8](https://github.com/Muratovnik/release-kit/actions/runs/37947853201)
ran once through `workflow_dispatch` for `v0.32.0` at commit
`b10a0708204da5aa40f3cd7362072e4abf42c1aa`, tree
`73eb357e4b4321fdaf762dac39ea1d5525672187`. The source jobs recorded:

| Native job | Host and Python | Base suite | Ruff | MCP suite |
| --- | --- | --- | --- | --- |
| Ubuntu `113878697115` | Ubuntu 24.04.5 LTS, x86_64, CPython 3.11.17 | Passed: 778 tests, four skips, 35.258 seconds | Lint passed; 109 files already formatted | Passed: 78 tests, three skips, 105.125 seconds; SDK 2.1.1 |
| macOS `113878697086` | macOS 26.6.2, arm64, CPython 3.11.9 | Passed: 778 tests, four skips, 126.256 seconds | Lint passed; 109 files already formatted | Passed: 78 tests, three skips, 174.153 seconds; SDK 2.1.1 |
| Windows `113878696709` | Windows Server 2025, AMD64, CPython 3.11.9 | Failed: 778 tests, one error, 20 skips, 624.438 seconds | Not reached | Not reached |

The quiet base runner prints aggregate outcomes and every skipped test, not
individual passing-test lines. Ubuntu and macOS list the same four Windows-only
base skips and three Windows/opt-in MCP skips. The explicit forkserver method
exists in the checked source and is absent from those exhaustive skip lists;
its execution remains a source-and-aggregate inference. The completed source
logs print MCP SDK 2.1.1 but no separate AnyIO version. Windows provisioned all
three pinned engines and enabled the opt-in, but its required real-engine MCP
acceptance was not reached after the base failure.

Windows failed in the review-added
[`test_recorded_worker_cleanup_handles_live_and_finished_native_children`](https://github.com/Muratovnik/release-kit/blob/b10a0708204da5aa40f3cd7362072e4abf42c1aa/tests/test_smoke_processes.py#L202-L220).
The trace identifies the second `stop_recorded_worker` call at line 220.
The first call had stopped the live child, both `wait` calls had returned and
the completed-returncode assertion had passed. The test then deleted its
`Popen` object and assumed that repeating cleanup must encounter an absent PID.
Instead, `os.kill` reported Windows access-denied error 5. This method and its
shared test helper were introduced together in review commit `a2027d5`; neither
file existed in baseline `0b3f05fe`. The issue extends D16's fixture portability
record, leaving all 42 finding IDs and severity counts unchanged. It is not a
D17/D18 installer failure or a newly identified shipped-runtime defect.

Microsoft documents that a terminated process object can persist while handles
remain open, and that [terminating an already completed process](https://learn.microsoft.com/en-us/windows/win32/api/processthreadsapi/nf-processthreadsapi-terminateprocess)
can report access denied. The failed job did not instrument the underlying
`OpenProcess` versus `TerminateProcess` branch, or identify a remaining-handle
owner. Those details remain unknown. The observed prior waits establish that
this child completed; releasing one Python object does not establish global
PID absence.

The bounded correction keeps the known completed child's native handle
through the second call and accepts error 5 only after opening a synchronization
handle and [observing its signaled state](https://learn.microsoft.com/en-us/windows/win32/api/synchapi/nf-synchapi-waitforsingleobject)
with a zero-time wait and successful handle closure. Missing status support,
a live or failed wait, or any open/wait/close error must preserve the original
refusal. The existing real live-child termination, no-late-write and resolver
rollback assertions remain required. The actual failing native job is retained
as the primary before-fix evidence.

Controlled Windows API responses exercised the unchanged public helper entry
point against ignored old and proposed source copies on Linux/CPython 3.11.17.
The old helper's one-test run produced an error in the positively confirmed
finished case: it re-raised error 5 without querying the signaled handle. Five
additional API-call assertions also failed; those are test diagnostics for the
same missing probe, not five separate behavioral defects. The run took 0.020
seconds. Three proposed-helper tests passed with no skips in 0.089 seconds,
covering signaled acceptance, non-signaled and failed waits, failed open/wait/close
operations, missing status support, other platform errors and positive-PID
validation. These are controlled API responses, not native Windows execution;
the held-Popen regression was not part of this shadow packet. The source and
refs stayed unchanged. Its receipt is
`.cache/review-release-lifecycle/windows-finished-worker-1r5mb4t5/shadow-summary.json`,
SHA-256 `f57124eb0eb50f51112e500f5d4fb0dae1f9e98a88f41703d6df1fbf2d49d48d`.
After application, the complete affected `test_smoke_processes` and
`test_version_source` modules passed on Linux/CPython 3.11.17: 31 tests in
23.413 seconds, with no skips. The actual owned runner returned 0 in 24.204
seconds without a cleanup exception. The API-status method, real live/finished
child method, no-late-write smoke and resolver rollback controls have named
passing results. The real child ran on Linux; this is not Windows handle-API
execution. Tracked files and refs stayed unchanged during the command; scoped
Ruff lint, format and diff checks passed. The applied two-file correction and
results are recorded in
`.cache/review-release-lifecycle/windows-finished-worker-1r5mb4t5/final-summary.json`,
SHA-256 `9e9804d95e4e31a2c889a7b02840ac9369f081056d0d7e4525e002bebf61dbae`.
No shipped runtime changed in this D16 follow-up. The subsequent canonical base
failed in a separate readiness fixture, described below. The next corrected
base and native workflow remain pending at this dated checkpoint; their actual
final-revision outcomes belong to PR #2.

The failed source job blocked shared candidate `113883445984` and the single,
unexpanded `candidate-smoke` job `113883447267`; both were skipped. The run's
artifact endpoint returned an empty list. No shared candidate or package stage
executed in run #8. The source reports state `cleanup=removed` on Ubuntu/macOS
and `cleanup=retained` on Windows, without separate later hosted filesystem
observations. These results do not qualify the corrected installed Windows
plugin, and do not erase the earlier package failures.

The Ubuntu/run summary is retained under
`.cache/review-changelog/native-linux-37947853201-dosxjckx`, with `summary.json`
SHA-256 `5c36327f4564edec8167f96d8dc9312f20cc4425e1a1a11355f2e5f38d20dae8`.
The macOS receipt is
`.cache/review-release-lifecycle/native-macos-37947853201-lt_tt_my/source-receipt.json`,
SHA-256 `5ef020fe231de0349cfab1f97710ef778e6eb833cd723395c1aa7343f78e72ff`.
The Windows failure receipt is
`.cache/review-publication-safety/windows-native-workflow-37947853201-0zpbcxz7/source-failure-summary.json`,
SHA-256 `1e132435771c223ccf53df626bd6c0164ebf4450938e733277688975f41d083e`.

### D13 candidate-smoke readiness and D6 failure diagnostics

A subsequent canonical base check on Linux / CPython 3.12.14 used `b10a0708`
with the three locally frozen D16 helper, regression and report changes. It ran
779 tests in 265.212 seconds, recorded five named skips and failed one assertion
in `SmokeProcessTests.test_all_candidate_wrappers_propagate_inner_cleanup_failure`.
The actual wrapper exited 1 after 270.577 seconds, finishing at
2026-10-09 15:27:54 UTC. Ruff was not reached. This base-only command did not
run the MCP or package qualification stages. Source and refs stayed unchanged
during the failed check.

The assertion at `tests/test_smoke_processes.py:145` found that the worker's
`ready` marker was absent. The parallel failure log did not retain which of
the onboarding, CLI or wheel subcases failed, child startup timing, or the
intermediate caught exception. It therefore does not establish the original
startup duration, the cause of the absent marker, or a product cleanup failure.
The three-wrapper method was added in review commit
[`29da007269636209ebda48b115839940990eab81`](https://github.com/Muratovnik/release-kit/commit/29da007269636209ebda48b115839940990eab81),
not in the original baseline. Its generated candidate started a one-second inner
command timeout before establishing worker readiness, and the worker published
its PID directly into the observed marker. The required live-worker precondition
was checked only after the candidate invocation. This additional test-precondition
case belongs to D13; the 42-item count and product finding counts are unchanged.

The correction publishes the PID atomically and uses the existing readiness
helper around the actual inner command. Startup has its existing ten-second
assertion bound; the inner operation timeout remains one second. The expected
inner `CleanupError` is saved inside that context so its successful-readiness
postcondition must finish before a separate `cleanup-refused` marker is written
and the same error is re-raised. The parent requires that marker alongside the
original live-worker, post-return write and error-type assertions. The permanent
regression delays each worker's startup by two seconds, longer than the operation
budget, and exercises onboarding, CLI and wheel wrappers. Production deadlines,
the 0.1-second outer test grace and one-second late-write wait are unchanged.

Four separate one-method controls ran on native Linux / CPython 3.12.14:

| Control | Actual outcome | Seconds |
| --- | --- | ---: |
| Exact old fixture with a real two-second startup delay | Failed the original missing-readiness assertion in the controlled onboarding case; one failure, no errors | 1.591 |
| Corrected permanent delayed-worker regression | Passed all three wrappers, each with readiness, confirmed inner refusal, outer `CleanupError` and a real late write after the trigger | 12.670 |
| Corrected fixture with only its readiness barrier removed | Failed the missing-readiness assertion even though the refusal marker existed; one failure, no errors | 1.371 |
| Worker that never publishes readiness | Failed the startup-confirmation marker despite an actual outer `CleanupError`; one failure, no errors | 10.518 |

These controls reproduce and discriminate the missing precondition; they do not
identify the unrecorded wrapper or timing in the original canonical failure.
The diagnostic harness subsequently confirmed its captured workers stopped;
its owned ancestors reported no unconfirmed cleanup, and source and refs stayed
unchanged. The frozen control receipt is
`.cache/review-release-lifecycle/smoke-worker-readiness-tui08b8_/control-summary.json`,
SHA-256 `c5e73b435c9eb4d5f771bc7b74730736ae63b05f4594e8bb5cb8216b411016b2`.
After application, the complete affected `test_smoke_processes` module passed
on Linux / CPython 3.11.17: 11 tests in 17.942 seconds, no skips. The owned
runner exited 0 after 18.334 seconds with no cleanup exception. The original
successful-command/no-background-write, output, cleanup-retention and real
live/finished-child checks also have named passing results. Tracked files and
refs stayed unchanged; scoped Ruff lint, format and diff checks passed.
The applied receipt is
`.cache/review-release-lifecycle/smoke-worker-readiness-tui08b8_/final-summary.json`,
SHA-256 `687c7720e54de4ae8a62ba8986ceaad4790f9d9848ec2db43e6f39ab5d7b24be`.
These Linux results do not qualify the separate native Windows handle branch.

**D6 diagnostic follow-up.** The original baseline runner already discarded
unittest's test objects when serializing failures and errors: lines 62–63 of
`tools/parallel_tests.py` at `0b3f05fe205efeb4151b581df8f4040d14b6d544` kept
only traceback strings. The correction prefixes the actual `test.id()`, including
subtest parameters, to each corresponding traceback. Failure/error counts,
unexpected-success handling, skip/completion verdicts and process ownership
are unchanged. A permanent regression executes identical-location failures
and errors through `_execute` and real `--jobs 1` and `--jobs 2` runs. Against
the old runner its one test produced 12 missing-identity assertion failures
and no errors; the corrected runner passed it in 0.117 seconds on Linux /
CPython 3.12.14. After application, the complete affected module passed on
Linux / CPython 3.11.17: nine tests in 1.672 seconds, no skips, owned-command
exit 0 after 2.546 seconds. Frozen runner/test bytes and refs stayed unchanged;
scoped Ruff lint, format and diff checks passed. Its receipt is
`.cache/review-publication-safety/runner-diagnostic-review-a1d2bazb/final-summary.json`,
SHA-256 `15de0e66f1000d9967db59d860335fc9131f308952a87c515217a16075554ee2`.
This repairs future diagnostics; it cannot recover the historical unknown
wrapper or establish its startup cause.

The failed base attempt remains preserved under
`.cache/review-root/windows-finished-worker-base-_20yx1fh`, including
`check.log`, `summary.json` and the identical before/after source and ref
inventories. Its failure is not replaced by the focused results. The next
canonical base and corrected native workflow remain pending at this dated
checkpoint; their actual final-revision outcomes belong to PR #2. The earlier
full `b10a0708` qualification and seven artifact identities below remain their
own completed evidence. These test and development-runner corrections do not
change the shipped payloads.

### Linux full distribution qualification at b10a0708

A separate canonical full check completed on the same clean `b10a0708204da5aa40f3cd7362072e4abf42c1aa`
source and `73eb357e4b4321fdaf762dac39ea1d5525672187` tree on native
Linux x86_64 / CPython 3.12.14. It exited 0 after 1,273.579 seconds and passed
all eight stages: base, MCP, build, CLI smoke, onboarding, wheel installation,
changelog and plugin stdio. The base suite ran 778 tests in 176.426 seconds
with five skips; Ruff lint passed and reported 109 files already formatted.
The MCP suite ran 78 tests in 989.914 seconds with three skips. A separate
query of that actual SDK environment reported MCP 2.1.1 and AnyIO 4.14.2.

The canonical build is a working-tree test build using `--allow-divergent`.
Its seven artifact hashes matched the separately held strict build, whose
recorded local commit `be486101914a4d006fe385fbe139935fa1042258` has the same
source tree as published `b10a0708`. Both strict-input inventories, before and
after the full check, matched the actual gate hashes. The local strict files
have the following measured sizes and SHA-256 identities:

| Artifact | Local bytes | SHA-256 |
| --- | ---: | --- |
| `release-kit-plugin.zip` | 426931 | `ecdc4ffc2800cb35628b742a0e507a4b2694cede6c20aa36c07d22df3a075745` |
| `release-kit-plugin.zip.sha256` | 89 | `42d8c9ff70b066d7f80569e59ca7d49e15d2244e2bb3ee8b9311d490d1dbaeb6` |
| `release.json` | 669 | `66361e36ac65f89dd67aba79189180d3c30c8e0754fdad54ecb5bc515376fe47` |
| `release_kit-0.32.0-py3-none-any.whl` | 170135 | `5d59395d503b691966e36a74e23b88feb5e70f4b53fd7195c3831ee8ec8b775e` |
| `release_kit-0.32.0-py3-none-any.whl.sha256` | 102 | `c16739d5f6a6bc3c3057b80e7087801d14abd9710a1ea12e0234ae23d99cfa4c` |
| `relkit.pyz` | 144961 | `928d6c7546a372277ad95abafd964addff36c467e7195ddc5996559a064b8f82` |
| `relkit.pyz.sha256` | 77 | `e2a216c6efafb9e5bb718abd33e1161b2a877dac9beeb3df7b4de37188b60ec9` |

Tracked source, refs and the strict files remained unchanged during the check.
The report recorded `cleanup=removed`, and the driver immediately observed
both the owned workspace and lock absent at 15:17:47 UTC. A read-only check
at 15:20:09 UTC found both paths visible again. The workspace inode changed
from 1505585 to 1579613 and the lock inode from 1505584 to 1510012 on device
27; their modification times were preserved and their change times were later.
The original identities had been captured during this same run at 15:01:50 UTC.
These observations establish changed identities after recorded absence, without
identifying an actor or mechanism. No observed path was cleaned or modified.
The records `visibility-during.json` and `later-visibility-observation.json`
are preserved beside the full-run summary. The qualification pass stands as
recorded; lasting cleanup and creation attribution remain unresolved. The full record is
`.cache/review-root/post-review-final-bt4qfous/summary.json`, with canonical
report `run-34nbqp6h.json`, SHA-256
`95a02f07949740e339d056c52f89a51c062e025eb5c68a0ba887833225ab727a`.

This qualifies the listed bytes on Linux/Python 3.12.14, including their actual
packaged stdio execution. It does not qualify Windows installation, undo native
run #8's failure, or cover the subsequent D16 test-helper correction. The next
source and native results will be recorded against their actual revision in
PR #2; no future result is assumed here.

### Windows runtime path units and qualification boundary (D17)

A bounded review after the D16 fixture correction found that the packaged
Windows launcher used `len(str(path))` in both loader decisions. Python strings
are [sequences of Unicode code points](https://docs.python.org/3.12/library/stdtypes.html#text-sequence-type-str),
whereas Windows [represents `WCHAR` values as UTF-16 code units](https://learn.microsoft.com/en-us/windows/win32/learnwin32/working-with-strings).
Characters outside the Basic Multilingual Plane require two such units.
Microsoft's [path documentation](https://learn.microsoft.com/en-us/windows/win32/fileio/maximum-file-path-limitation)
also describes filename strings as sequences of `WCHAR` values. Counting Python
characters can therefore understate the existing Windows loader budget without
changing the path or its Unicode normalization.

The exact baseline launcher and the launcher at published `f36bb635` had the
same SHA-256, `6b1ff9590dd6aa245cb9e5739a52d815f161b1389646bd70ccf9fd812a286b8c`.
An actual-helper control on Linux/Python 3.11.17 used a Windows path model and a
controlled alias provider. A runtime path plus the existing compiled-file
allowance measured 201 Python code points but 291 UTF-16 units; the helper
accepted it without attempting an alias. An ASCII path of the same 291-unit
length was refused, and a 201-unit BMP control was accepted. The alias branch
also accepted a returned path requiring 297 units, while a valid 103-unit alias
was accepted as expected. All five controls produced the same outcomes on the
baseline and current whole-file snapshots. The receipt is retained under
`.cache/review-release-lifecycle/windows-utf16-loader-nd478ew4`.

This confirms a baseline product guard defect, distinct from D16's ordinary
smoke-fixture padding. The controls did not create those paths on Windows or
perform a native DLL import. The correction measures both direct and alias paths
using UTF-16-LE with `surrogatepass`, preserving opaque path contents, the
existing 260-unit threshold, the 70-unit compiled-file allowance and explicit
refusal. POSIX behavior is unchanged. The ordinary smoke fixture includes a
non-BMP character and measures its padding in the same Windows units.

The new actual-loader method failed three assertions against the original
implementation in 0.020 seconds. The corrected complete plugin-build module
passed 13 tests on Linux/Python 3.11.17 in 8.676 seconds and Python 3.12.14 in
4.554 seconds, each including one explicit native Windows 8.3 skip. Controls
cover both false-acceptance branches, valid aliases, ASCII/BMP/non-BMP paths,
opaque surrogate contents and unchanged POSIX behavior. A separate control
restored only code-point padding in an ignored copy of the new Unicode smoke
fixture: the real corrected loader required an alias for its 267-unit runtime
and the permanent regression failed. Corrected padding produced 254 units and
returned the original runtime without an alias; the same test passed. Scoped
Ruff, format and diff checks passed. These receipts are retained in the
`correction` directory under the D17 workspace above; its final summary has
SHA-256 `b15c3233d35df087ab14b8a41b1bad66f4cf08b131cd4ea38baafeaefb84639a`.
This completes the focused correction evidence, without claiming native DLL
loading or package acceptance of the changed launcher.

The subsequent canonical base command checked the exact four-file D17
launcher/smoke/test/report delta over parent `f36bb635` on Linux/Python 3.12.14.
It passed 776 tests in 90.007 seconds, including five skips, and Ruff lint plus
the 109-file format check; total command time was 91.297 seconds, exit 0.
The recorded source and refs remained unchanged. Optional MCP discovery and
package phases were not part of this command. Its receipt is
`.cache/review-root/utf16-loader-base-k0or89p2/summary.json`, SHA-256
`6296b6f278aff61830f0559143ef67bcd23ee3442a39cc41b14110718653a34d`.
Those checked bytes were subsequently recorded in local commit
`12b2d3bc1c96ba0882d50471ab86656a51cdce0b`, tree
`b48d69960596804ed6472d7e4325e29deb8424aa`. This is the recorded local commit
identity, not a claim of its publication or hosted qualification.

### Windows wheel-cache compatibility boundary (D18)

Native workflow #7's Windows package run reached the plugin's nested `uv sync`
and failed while installing `pywin32==312`, before successful stdio startup.
The recorded cached script had 265 UTF-16 units with its extended prefix, or
261 without it. Its scripts parent had 238 units in the displayed ordinary
form, or 242 with that prefix. uv 0.12.24 reported `Trivial strip failed`
between those two forms. Both the Windows source job and the package job's
outer SDK bootstrap installed `pywin32` successfully using the same uv version
at shorter ordinary paths. Those positive observations vary both path depth
and spelling; they do not independently isolate one from the other.

Source review of [uv 0.12.24](https://github.com/astral-sh/uv/releases/tag/0.12.24),
commit `5411378eb76dc1ea1ad90aeb10e84e997e5bbd96`, and its locked
[dunce 1.0.5 path implementation](https://docs.rs/dunce/1.0.5/src/dunce/lib.rs.html)
identified the boundary. The [wheel-script validation](https://github.com/astral-sh/uv/blob/5411378eb76dc1ea1ad90aeb10e84e997e5bbd96/crates/uv-install-wheel/src/wheel.rs#L193-L217)
runs before linking or copying the wheel and calls a
[relative-path helper](https://github.com/astral-sh/uv/blob/5411378eb76dc1ea1ad90aeb10e84e997e5bbd96/crates/uv-fs/src/path.rs#L384-L408)
that independently simplifies the child and parent. Disk-cache resolution
canonicalizes the physical path.
dunce preserves the extended prefix when the full Windows path exceeds its
260-unit simplification budget, while the shorter parent can lose that prefix.
The wheel-script comparison can consequently receive different prefix forms
for a file and its actual parent. Merely supplying an ordinary path or 8.3
alias does not establish that the physical cached descendants fit this budget.

The exact lock's 30 registry wheels compatible with CPython 3.11 / win_amd64
were downloaded or reused, verified against their recorded SHA-256 and size,
and inspected as ZIP files in 38.459 seconds with no errors. This was a
compatible superset including optional packages, not a claim that 30 packages
were installed. Only pywin32 contained the relevant `.data/scripts` or
`.data/data` file entries: two scripts, with a maximum path length of 47 UTF-16
units. uv's default archive directory adds 29 units, including its 16-character
archive ID, giving the required 76-unit suffix allowance. The launcher does
not enable the separate content-addressed-cache preview mode. This inventory
does not qualify other Python/architecture wheel variants or a native install.
Its receipt is
`.cache/review-release-lifecycle/windows-uv-caller-mkw9k130/locked-wheels/inventory.json`,
SHA-256 `e9163c1f68af9da233690e2b4f9e3d6145637334b740d7ebddf24abb5cef43e1`.

The correction adds a separate pre-initialization check that
the full verbatim disk-cache path plus those 76 units fits the 260-unit
simplification boundary. For the current layout this permits a physical cache
path of 180 units without its four-unit prefix, or a plugin root of 165 units.
The cache stays under the installed plugin's `.runtime/cache`; an installation
that does not fit fails with a shorter-path instruction before alias selection,
environment setup, locking, runtime creation or uv invocation.
This is independent of D17's DLL-loader and alias budget. It does not introduce
a new UNC-path restriction, relocate cache ownership or select a different uv
version. The plugin documentation removes its unbounded extended-cache claim.
The ordinary Windows smoke target changes from 170 to 160 UTF-16 units while
preserving a long path, spaces and a non-BMP character.

The complete affected module passed on native Linux/Python 3.11.17 and 3.12.14:
15 tests in 13.568 and 15.805 seconds respectively, each including one explicit
native Windows 8.3 skip. The new
`test_windows_installer_checks_physical_disk_cache_in_utf16_units` exercises
the exact 260/261-unit boundary, the observed 265-unit path, non-BMP and opaque
UTF-16 units, ordinary and verbatim disk paths, unchanged UNC handling and the
separate DLL budget. The new
`test_windows_installer_refuses_before_creating_runtime_or_lock` uses a real
built package to verify refusal before state creation or uv execution and
preserves read-only `--check`. The existing smoke-path regression verifies
that its ordinary fixture fits both actual guards without an alias. These
controlled Windows-path checks run on Linux; they do not execute Windows
filesystem or installer APIs. All three implementation/test files stayed
unchanged during both commands; scoped Ruff lint, format and diff checks passed.
The normalized receipt is
`.cache/review-release-lifecycle/windows-installer-guard-sh6pn8m1/summary.json`,
SHA-256 `c6e5e2374bc14cc27b1ebae483f31e3da2dbd43da5d10d2b13c334e191bbe6c7`.
Native workflow #7 supplies the actual before-fix failure. Native cold,
concurrent and repeated startup of the corrected payload remains pending.

The upstream source receipt is
`.cache/review-publication-safety/uv-upstream-01224-97cz_jdi/receipt.json`,
SHA-256 `dd55d4adb44fcac86b05c9107621c9929d19171334abf6f05d1991aecafe6f48`.
The native failure receipt is
`.cache/review-publication-safety/windows-native-workflow-37939010141-4ixvsrg7/package-failure-summary.json`,
SHA-256 `ba16e2eb06bc91e7cb0db09f25d930a1a897cde7b906fb358765a4d24e085071`.
The earlier failed run is preserved; this source diagnosis does not change its
verdict or turn its unreached successful stdio exchange into a pass.

### Hosted qualification at f36bb635 before D17

[Native workflow run #7](https://github.com/Muratovnik/release-kit/actions/runs/37939010141),
run `37939010141`, attempt 1, `workflow_dispatch`, input `v0.32.0`, checked
published commit
`f36bb635de47c7ed5f398b766673b3f997944728`, tree
`e8db551bc991eb88af6abcb2b646d81916e9837f`, before the D17 correction.
All three source jobs passed. The overall run completed with failure in the
Windows package's plugin-stdio phase; it is not an all-platform package pass.

| Actual source job | Native runtime | Base result | MCP result |
| --- | --- | --- | --- |
| Linux `113848284019` | Ubuntu 24.04.5, x86_64, CPython 3.11.17 | Passed: 775 tests, four named skips, 40.716 seconds | Passed: 78 tests, three skips, 157.796 seconds |
| macOS `113848284401` | macOS 26.6.2, arm64, CPython 3.11.9 | Passed: 775 tests, four named skips, 128.743 seconds | Passed: 78 tests, three skips, 167.352 seconds |
| Windows `113848284287` | Windows Server 2025, AMD64, CPython 3.11.9 | Passed: 775 tests, 20 named skips, 640.313 seconds | Passed: 78 tests, six skips, 321.399 seconds |

All three source jobs passed Ruff lint and the 109-file format check and
reported MCP SDK 2.1.1. The Windows log again names the real-engine
`test_update_with_real_engines_without_windows_processor_environment` as
successful. On Linux and macOS, complete base discovery and the exhaustive four
Windows-only skips support execution of the explicit forkserver and ordinary
plugin-path fixture controls. These are source-verified aggregate execution
inferences, not individually printed base-test successes.

The shared candidate job `113855209495` executed the strict
`python tools/build_release.py dist` command without `--allow-divergent` and
uploaded artifact `11620948290`, `release-candidate`, archive size 730340 bytes,
digest `sha256:ccec6ef82f7e511622975a753a4feff860205b631c9a5245d0802ef7654d0bf7`.
Its metadata binds the archive to this run and commit. All three package jobs
downloaded that artifact and logged the matching digest. Their seven input
hashes match the separately built strict files at `f36bb635` and the earlier
`1428dbb0` hash table below. Per-file sizes were measured locally; native
content identity is established by the matching full SHA-256 values, not an
independent per-runner size measurement.

| Actual package job | Result against the shared candidate | Report |
| --- | --- | --- |
| Linux `113855269814` | All five phases passed | `run-tj_vyvaj`, passed |
| macOS `113855269908` | All five phases passed | `run-6pweiyy9`, passed |
| Windows `113855269794` | `cli-smoke`, `onboarding`, `wheel-install` and `changelog` passed; `plugin-stdio` exited 1 | `run-yx68yiqj`, failed |

The successful Linux and macOS package logs record MCP 2.1.1 and AnyIO 4.14.2.
The Windows result supplies actual native acceptance of the D16 changelog
fixture correction. Its later plugin phase failed during installation of
`pywin32==312`, before a successful stdio session. The installer reported
`Trivial strip failed` for an extended-prefix `\\?\D:\...` file and its
ordinary `D:\...` scripts parent. This observed installation failure is not
attributed to D17; its confirmed upstream compatibility boundary and implemented
correction are recorded separately as D18 above.
The successful source and Linux/macOS package reports state `cleanup=removed`;
the failed Windows package report states `cleanup=retained`. No independent
later filesystem observation was made on those hosted runners.

Raw logs and normalized receipts are retained under
`.cache/review-changelog/native-linux-37939010141-gb0y9jyf`,
`.cache/review-release-lifecycle/native-macos-37939010141-rh7d3j0c` and
`.cache/review-publication-safety/windows-native-workflow-37939010141-4ixvsrg7`.
This run qualifies only its recorded source and earlier seven-file set. It
cannot qualify the later D17/D18 runtime corrections or their changed plugin payload.
The clean pre-dispatch local snapshot and authorized local edits are separate
source states. Native desktop-client discovery remains unverified.

### Native and canonical qualification at 1428dbb0

[Manual workflow run #6](https://github.com/Muratovnik/release-kit/actions/runs/37933011696)
(run `37933011696`, attempt 1, `workflow_dispatch`, input `v0.32.0`)
checked clean published commit `1428dbb098eed84e4dc3242ecd49181eebafc026`,
tree `94490929caf372528efbb5748dd52f402804c676`. All three native source
jobs passed. The overall workflow nevertheless failed in the later Windows
package changelog check; it is not an all-platform package qualification.

| Actual source job | Native runtime | Base result | MCP result |
| --- | --- | --- | --- |
| Linux `113828117369` | x86_64, CPython 3.11.17 | Passed: 774 tests, four named skips, 38.872 seconds | Passed: 78 tests, three skips, 126.965 seconds |
| macOS `113828117387` | macOS 26.6.2, arm64, CPython 3.11.9 | Passed: 774 tests, four named skips, 103.313 seconds | Passed: 78 tests, three skips, 120.556 seconds |
| Windows `113828117076` | Windows Server 2025, AMD64, CPython 3.11.9 | Passed: 774 tests, 20 named skips, 564.344 seconds | Passed: 78 tests, six POSIX skips, 307.545 seconds |

All three source jobs passed Ruff lint and the 109-file format check and
printed SDK 2.1.1. The Windows log explicitly records
`test_update_with_real_engines_without_windows_processor_environment` as
successful, after provisioning all three pinned tools with the opt-in enabled.
The native alias control exercised the allowed refusal branch: no usable
native short alias was available, and the deep runtime was refused without
skipping the test.

The macOS MCP log names both transient and persistent EPERM controls as
successful, alongside pending-notification, nested-cancellation and
unconfirmed-cleanup controls. Its complete base pass has exactly the four
named Windows-only skips. Together with exact-source discovery, this supports
execution of the synchronous D15 controls, the five previously failing
timeout methods and the explicit two-worker forkserver oracle. Linux has
the same exhaustive four-skip boundary. These base results are source-verified
execution inferences, not individually printed success records; the original
failed macOS group composition remains unproved.

The shared Linux `candidate` job `113833863935` passed and uploaded
`release-candidate`, artifact `11618500363`, archive size 730340 bytes, digest
`sha256:9ce8b951a0f9329decb9558621c7c44dc01f88db79568f87b0304b144da66c9f`.
Artifact metadata binds it to this run and commit. All three package jobs
downloaded that exact artifact and recorded the matching actual digest;
they used `--assets dist --version 0.32.0` without rebuilding.

| Actual package job | Result against the shared candidate | Report |
| --- | --- | --- |
| Linux `113833947248` | All five phases passed | `run-chrw2swf`, passed |
| macOS `113833947200` | All five phases passed | `run-1v548a0j`, passed |
| Windows `113833947292` | `cli-smoke`, `onboarding` and `wheel-install` passed; `changelog` exited 1; `plugin-stdio` did not run | `run-jpxoa2k8`, failed |

The Windows failure was `adding aliases changed extraction of an English
historical entry`. Its failed result and unexecuted stdio phase are preserved.
Linux and macOS package results record all seven hashes unchanged; the Windows
failed report also records the same seven input hashes. All match the separate
strict build at this source. Hosted source reports and the two successful
package reports state `cleanup=removed`; the failed Windows package report
states `cleanup=retained`. No independent later filesystem observation was
made on those hosted runners.

A separate complete canonical Linux x86_64 / Python 3.12.14 run at this same
clean source passed all eight phases with exit 0 in 1077.065 seconds:
774 base tests in 143.923 seconds with five skips, Ruff lint/format for 109
files, 78 MCP tests in 831.613 seconds with three skips, the test build and all
five actual package phases. Its isolated SDK reported MCP 2.1.1 and AnyIO
4.14.2. Report `run-3muaddpu` identifies a
`working-tree-distribution-check`; recorded source and refs stayed unchanged.
The separate strict build passed in 0.779 seconds without
`--allow-divergent`; all seven files match the canonical report and the
hosted package results. The canonical gate also confirmed its artifact
inventory unchanged. Its immediate observer recorded the lock and workspace
absent; the later visibility observation below remains a separate forensic
limit.

| File at 1428dbb0 | SHA-256 |
| --- | --- |
| `release-kit-plugin.zip` | `2c21f8c15c34fbea9f5d445992401c5ffba5426bef852ac79888df51372f9265` |
| `release-kit-plugin.zip.sha256` | `d9d6607590ee11454ddbd91a45c5e627b09fa540b465e53eeb3169b34e7d6a4f` |
| `release.json` | `2116d8dc53d90a045cdc200df894627714d5b1be18677ec3fc6a6a4584c035ef` |
| `release_kit-0.32.0-py3-none-any.whl` | `5d59395d503b691966e36a74e23b88feb5e70f4b53fd7195c3831ee8ec8b775e` |
| `release_kit-0.32.0-py3-none-any.whl.sha256` | `c16739d5f6a6bc3c3057b80e7087801d14abd9710a1ea12e0234ae23d99cfa4c` |
| `relkit.pyz` | `928d6c7546a372277ad95abafd964addff36c467e7195ddc5996559a064b8f82` |
| `relkit.pyz.sha256` | `e2a216c6efafb9e5bb718abd33e1161b2a877dac9beeb3df7b4de37188b60ec9` |

These results qualify their recorded source and payload only. A later
report/development-smoke correction is not a rerun of these source suites;
transfer of the package identities requires an actual strict rebuild matching
all seven files. Windows package acceptance remains open until the corrected
reviewed candidate completes the missing checks. Native desktop-client
discovery remains unverified on every platform.

### Hosted native workflow at 93718d0c and follow-up corrections

The first authenticated hosted run for this reviewed candidate was
[manual workflow run #5](https://github.com/Muratovnik/release-kit/actions/runs/37927315557),
run ID `37927315557`, event `workflow_dispatch`, branch
`fix/release-audit-20261009`, input `v0.32.0`. Checkout and canonical source
results identify commit `93718d0cfd6f1e5e1d8efae8177b8e8011910e41`, tree
`d1dc0bc36e0afd85aabfa6ba93c823b83516c021`, with clean source. The run
finished with failure; it is not a successful native qualification.

| Actual source job | Native runtime | Base result | Ruff / MCP result |
| --- | --- | --- | --- |
| Linux, job `113809252844` | Ubuntu 24.04.5, x86_64, CPython 3.11.17 | Passed: 762 tests including four skips, 46.606 seconds, four workers | Ruff check and 108-file format check passed; MCP 76 tests including three skips passed in 132.038 seconds; SDK 2.1.1 was printed |
| macOS, job `113809252867` | macOS 26.6.2, arm64, CPython 3.11.9 | Failed: 762 tests, one failure and four errors, 130.173 seconds | Neither Ruff nor MCP ran after the base failure |
| Windows, job `113809252498` | Windows Server 2025, AMD64, CPython 3.11.9 | Failed: 762 tests, 11 failures and two errors, 560.781 seconds | Neither Ruff nor MCP ran after the base failure |

The failed parallel results did not print skip totals; none is inferred for
macOS or Windows. The Linux canonical result reports `cleanup=removed`; the two
failed source results report retained workspaces. These are hosted runner
reports, without an independent later filesystem observation on those runners.
The Windows provisioning step successfully resolved all three pinned tools and
the source command received `RELKIT_TEST_REAL_ENGINES=1`. The named Windows
MCP sync acceptance test was nevertheless not reached because the base gate
failed.

The shared `candidate` job `113812715179` was skipped. The dependent
`candidate-smoke` job `113812715876` appears once as an unexpanded, skipped
matrix. The run's artifact list is empty. No shared candidate was built,
uploaded or package-qualified by this run.

Linux forkserver coverage is a source-verified inference with a precise bound.
The base runner prints aggregate results, not individual successful test names.
Its four skips exactly exhaust the four unconditional Windows-only tests:
native junction handling, native 8.3 aliases, Windows descendant cwd teardown
and the native Windows architecture query. The discovered explicit forkserver
case therefore did not take a capability skip. That case requires two distinct
worker PIDs, the selected forkserver start method, normal timeout cleanup and
retention after induced cleanup denial. This closes the hosted Linux execution
gap for that maintained oracle; it is not a named macOS result or desktop-client
acceptance.

**D15 — process-group observation.** Three direct macOS traces contain
`PermissionError: [Errno 1]` from the process-group probe; two further nested
timeout controls ended in the conservative forced/abnormal-cleanup refusal.
The original baseline also treated such a probe error as an immediate terminal
cleanup failure. The
[Apple XNU group-signal implementation](https://github.com/apple-oss-distributions/xnu/blob/f6217f891ac0bb64f3d375211650a4c1ff8ca1ea/bsd/kern/kern_sig.c)
filters zombie members before returning POSIX `EPERM` when no signalable
member was counted. This supports a transient exiting-group explanation; no
per-PID snapshot was captured on the failing runner, so the exact group
composition is not retrospectively proved.

The synchronous and MCP helpers now keep `PermissionError` in the
present-or-unconfirmed state and poll within the existing deadline. Only actual
`ESRCH` confirms disappearance. Existing lifetime EOF, accepted exit statuses,
forced-termination refusal and recovery retention remain in force. Persistent
denial still refuses cleanup; other operating-system errors are not converted
to success. Actual Linux child controls inject only the permission transition:
the synchronous three-method regression went from two subcase errors and one
failed escalation assertion to three passes in 0.645 seconds; the MCP
two-method regression had the same two errors and one failed escalation
assertion before correction, and five selected methods passed afterward in
7.195 seconds. Those MCP controls also cover delayed exit notification,
abnormal nested cleanup and forced termination. The persistent-denial
distribution control keeps its real lock and report while a raw same-group
worker, which has closed inherited lifetime writers, can still write.

**D16 — Windows fixture portability.** The 13 Windows failure/error records
from run #5 fall into five test-fixture subcases, not 13 product defects:

| Fixture assumption | Evidence and corrected oracle |
| --- | --- |
| Default text writes matched committed Git bytes | Seven release-input assertion records shared this cause. A real-Git control reproduced a clean status with 33 raw inputs differing from their normalized Git blobs. Fixture writes and local Git configuration now establish explicit bytes. A separate regression rejects normalized CRLF worktree bytes but accepts actually committed CRLF; the same controlled suite changed from seven failures among ten methods to 11 passing methods. |
| Native text streams always used LF | The CLI diagnostic assertion now compares the full expected line sequence; its structured multiline cause remains exact. The fake draft generator emits explicit UTF-8 bytes. Internal CRLF preservation, source/history/policy bytes and alias refusal remain separate exact assertions. Seven selected controls passed locally. |
| A Windows link's stored target spelling equalled the supplied path string | The snapshot assertion captures the actual native link target before materialization, then requires that exact spelling in an ordinary snapshot file and no traversal into the private directory. |
| A successful short-path query always shortened a path | The original baseline native 8.3 test assumed mandatory shortening. The revised native test checks the same installed directory and the actual loader budget: use a suitable native alias, or require the existing explicit refusal. Lack of a usable short alias no longer makes this test skip. |
| Cleanup of an already absent positive PID always raised `ProcessLookupError` | Two review-added fixtures instead received Windows error 87 in their cleanup. A shared test-only helper accepts that Windows absent-PID result, requires a positive recorded PID, and preserves Windows access/handle errors and non-Windows failures. Existing no-late-write and rollback-byte oracles remain intact. |

[Microsoft documents](https://learn.microsoft.com/en-us/windows/win32/api/fileapi/nf-fileapi-getshortpathnamew)
that the short-path API may succeed with the original long name;
[Python 3.11 documents](https://docs.python.org/3.11/library/os.html#os.readlink)
the Windows substitution-path spelling returned by `os.readlink`.
These corrections change test fixtures and their platform oracles, not runtime
newline handling, strict-build byte checks, launcher behavior or volume settings.
The native 8.3 test predates this review; the other affected source fixture
methods listed above were added during it. The focused release-input/link/alias packet ran 15 tests
including the one native Windows skip on Linux. Run #6 subsequently passed
these source corrections on native Windows and macOS. Its separate Windows
package changelog failure is retained in the newer qualification record above.

**Later package fixture in D16.** Run #6 exposed an additional review-added
fixture assumption, not another product finding. The packaged changelog smoke
compared a generated LF entry with history installed through `Path.write_text`;
the Windows text write translated that history to CRLF. The actual `1428dbb0`
CLI and pinned git-cliff 2.14.1 reproduced the same error in 6.782 seconds when
only that historical write received controlled Windows newline semantics.
A one-line `write_bytes` correction establishes the expected LF fixture while
retaining the exact entry-equality assertion. Actual complete smoke controls
then passed on Linux/Python 3.11.17 in 13.094 seconds and Python 3.12.14 in
16.485 seconds. Separate LF/CRLF extraction and export controls confirmed
unchanged source bytes and preservation of internal line endings under the
existing terminal-LF export contract. No runtime parser or normalization
behavior changed. Run #7 subsequently passed the corrected changelog phase on
native Windows. It reached plugin-stdio and failed during dependency
installation, as separately recorded above.

**Plugin startup fixture in D16.** A bounded review of that unreached stdio
phase found a separate baseline assumption in `tools/smoke_plugin.py`: for an
ordinary short parent, its Windows padding made the installed package path
180 characters long. The maintained launcher's runtime suffix and compiled-file
allowance raised the loader budget to 264 characters, exceeding its supported
260 and requiring a usable short alias. This is a confirmed fixture constraint,
not an observed failure of the unreached hosted phase. The fixture now pads to
170 characters, yielding 254 under the same loader contract. It retains a long
path with spaces inside the caller's workspace; an already too-long parent
remains owned and can still produce the existing explicit refusal.

A permanent regression calls the actual fixture destination helper and shipped
loader, with any alias lookup forbidden for ordinary startup. The helper first
preserved the original 180-character behavior and failed that test; the
170-character correction passed. The complete affected module then passed
12 tests on Linux/Python 3.11.17 in 9.240 seconds and Python 3.12.14 in
3.331 seconds, each with the native Windows 8.3 test explicitly skipped.
Existing alias and refusal controls remain unchanged. These controlled results
do not claim native Windows startup or cache installation beyond `MAX_PATH`;
run #7 reached dependency installation but did not complete a Windows stdio
session. This D16 fixture correction
changed neither runtime launcher code nor the product finding count; D17's
separate runtime-unit defect is recorded above.

**D13 — ordinary launcher fixture allowance.** A later precommit Linux
Python 3.11 base check ran 769 tests in 182.599 seconds and stopped with one
error in the managed projection status/argv/cwd fixture. The five-second outer
deadline expired; cleanup completed, and the original startup/completion timing
was not captured. Ruff did not run after that base failure. This was a failed
source check, not a completed package qualification.

The ordinary functional fixture had no latency assertion, while its sibling
launcher harness already allowed 120 seconds and the supported product Git
lookup allows 30 seconds. Only those two ordinary harnesses now share the
existing 120-second allowance. Production deadlines and actual timeout or
cancellation tests are unchanged. A controlled real projection delayed its
startup by six seconds: the exact old fixture failed both invocation subcases
with `TimeoutExpired` in 10.172 seconds, with started-but-not-finished markers;
the corrected fixture completed both with the same status/argv/cwd assertions
in 17.826 seconds. The complete launcher module then passed all 14 methods on
Python 3.11 and 3.12 in 22.654 and 10.040 seconds. This demonstrates the
unsupported test budget; it does not establish what caused the original loaded
run to exceed five seconds. Receipts and the exact before snapshot are under
`.cache/review-release-lifecycle/launcher-fixture-ready-195lyt_h`.

**D6 — skipped subtest qualification.** Actual miniature suites run through
the unchanged source runner and MCP discovery reproduced both directions of
the counting error: one method with two skipped subtests, or two methods with
four skipped subtests, falsely passed; mixed executed/skipped cases could
instead be refused as entirely skipped. The MCP gate could also misclassify a
real assertion failure followed by two skip events as an all-skipped refusal.
These controls exercise the gate, not the full SDK integration suite.

Both gates now record successful cases, expected failures and successful
subtest callbacks. A real failure or unexpected success takes precedence over
zero-completion refusal. Source logs also identify each skipped method or
subtest and its reason. The permanent source controls exercise actual
one-worker and two-worker paths, with at least two discovered methods in the
parallel fixtures; completion-only cases add an entirely skipped method, not
an ordinary pass that could mask the oracle. They cover mixed and entirely
skipped subtests, class/module setup skips, expected failure and real failure.
The initial new source regression packet produced seven failed assertions and
no errors on the old runner. The final complete runner module passed eight
tests on Python 3.12.14 in 1.487 seconds. The three new MCP gate methods failed
before correction with three failures and four errors; the complete nine-method
`DiscoveryTests` class then passed on Python 3.11.17 and 3.12.14 in 0.118 and
0.075 seconds. Receipts are under
`.cache/review-publication-safety/skipped-subtests-f1ha2s0h` and
`.cache/review-root/subtest-gates-jcjmwzkk`. This extends D6 without adding a
new finding. The subsequent `1428dbb0` candidate passed all three hosted
source jobs and complete canonical Linux qualification, as recorded above;
its Windows package failure remains separate. Older successful runs are not
transferred to changed runtime or test bytes.

The raw hosted logs and normalized observations are retained under
`.cache/review-changelog/native-linux-37927315557-pgfpp4r2`,
`.cache/review-release-lifecycle/native-macos-37927315557-xre7_wsi` and
`.cache/review-publication-safety/windows-native-workflow-37927315557-sv7x6bhv`.
Local before/after receipts are under `macos-eperm-fix-92ukplzr`,
`macos-eperm-4xlg8_ux` and `windows-fixture-cleanup-lj8ldqnp` in their
respective review workspaces. The earlier complete Linux source/package
qualifications below remain historical evidence for their exact commits and
bytes; they do not qualify the later D15/D16 runtime/test revision.

### Previously qualified packaged payload at cc7c26e

The revised packaged payload was built from clean published commit
`cc7c26e4711bd5acc342b230358345fdfb6223ef`, Git tree
`c9a89d7ebc62da15fe26d6184a99b23fa5d2eabe`, version 0.32.0. Its delta from the
fully source-qualified `b2cec0f2` is limited to the manual Windows workflow,
one compatibility wording correction in packaged `docs/mcp.md`, and this report.
Runtime and test sources are unchanged. The documentation correctly names the
executor, which is the bundled CLI in installed plugin mode, as the component
whose version controls Actions next/prepare support; the threshold is unchanged.

A separate strict build, without `--allow-divergent`, passed with exit 0 in
2.554 seconds. The documentation change updated the plugin ZIP, its sidecar and
`release.json`; the wheel, zipapp and their sidecars stayed byte-identical to the
initially qualified set. The complete revised seven-file set was then supplied
to two real `--assets` checks with `--version 0.32.0`.

| Check | Linux Python 3.11.17 | Linux Python 3.12.14 |
| --- | --- | --- |
| Actual provided-assets qualification | Five phases passed, exit 0; 164.498 seconds | Five phases passed, exit 0; 135.311 seconds |
| Distribution report | `run-wxhf5r0x` | `run-olpxorvn` |
| SDK identity | MCP 2.1.1; AnyIO 4.14.2 | MCP 2.1.1; AnyIO 4.14.2 |
| Artifact comparison | All seven match the strict-built set; unchanged after checks | All seven match the strict-built set; unchanged after checks |

Each run executed `cli-smoke`, `onboarding`, `wheel-install`, `changelog` and
`plugin-stdio` against the supplied files. These were provided-assets checks;
the complete source suites remain associated with the separate `b2cec0f2` runs
below. Source identity and all 25 local refs were unchanged before and after both
runs. Independent comparisons matched each report to the actual seven strict
files, including sizes and hashes. Both reports record successful cleanup, and
the immediate driver observations found the lock and workspace absent.

An initial Python 3.12 wrapper invocation refused preflight in 0.328 seconds
because its explicit owned work directory had not been created. No product phase
ran; the unchanged check then ran successfully from a fresh, correctly prepared
owned parent. That preserved setup failure is not another product finding.

### Previously qualified cc7c26e artifact identities

| File | SHA-256 |
| --- | --- |
| `release-kit-plugin.zip` | `119ed7e81340781e14ebe1b27d8136b6988d8972ee835944a7b0433a32708790` |
| `release-kit-plugin.zip.sha256` | `7ec34c3d3cccdd3c0fce2531cdc3484ad1c0dbbb0c70aca39c104f4bff22205d` |
| `release.json` | `43301a05185b8d9a3c5235d59c6e54c461d3d8d54d33719327ebb47c1597d986` |
| `release_kit-0.32.0-py3-none-any.whl` | `8efb4ce6866307b606736d68013243e16d77cdf3ed8764affc78b9c855cb302b` |
| `release_kit-0.32.0-py3-none-any.whl.sha256` | `26d8c7aa1ef9f05e04fde26325fb762c3a7ad333c84b6c401516b9a2f02bd75c` |
| `relkit.pyz` | `909b0a9ac01ea76ca69faef58811cea9aec2effad272d4614bc69613fb3ffce7` |
| `relkit.pyz.sha256` | `17a188b0e8e3ea31ca8f32b8885f8d8cb341522a22a0321306c55d2c326cc8ef` |

These are the revised payload identities qualified on both Linux interpreters.
A subsequent report-only revision can carry this package evidence only after a
strict rebuild matches all seven files. Its final remote identity and publication
audits belong to the PR delivery record, separately from the exact qualification
commits recorded here.

### Canonical source and package qualification at b2cec0f2

The complete canonical Linux checks ran against clean source commit
`b2cec0f2fafccae396147583e75d399a508bb6dd`, Git tree
`8fa0bc5a5c8ca905291f4aae33e083a5ae438274`, version 0.32.0. This candidate includes
the integrated MCP exit notification correction, explicit MCP and synchronous
fixture readiness, forkserver acceptance extension, generator diagnostics and
generated release notes. Both native Linux x86_64 runs passed all eight phases,
on Python 3.11.17 and Python 3.12.14. Their isolated SDK environments used MCP
2.1.1 and AnyIO 4.14.2. Source identity and all 25 local refs were unchanged
before and after each run. Earlier unsuccessful attempts remain separately
recorded below; these results do not substitute for native Windows, macOS,
forkserver or desktop-client acceptance.

| Check | Linux Python 3.11 | Linux Python 3.12 |
| --- | --- | --- |
| Base source suite and Ruff | Passed: 762 tests including five skips, 93.940 seconds; Ruff check/format passed | Passed: 762 tests including five skips, 98.948 seconds; Ruff check/format passed |
| MCP source/SDK suite | Passed: 76 tests including three skips, 864.447 seconds | Passed: 76 tests including three skips, 785.734 seconds |
| Canonical working-tree test build | Passed | Passed |
| Five actual package phases | All five passed | All five passed |
| Before/after artifact inventory | Seven files unchanged; equal to strict-built set | Seven files unchanged; equal to strict-built set |
| Complete eight-phase canonical distribution check | Passed, exit 0; 1108.739 seconds | Passed, exit 0; 1022.604 seconds |

A separate strict build at this clean source completed with exit 0 in 1.683
seconds, with source and refs unchanged. All seven actual files match both the
new build receipt and the preceding `28eff54` strict set, whose build completed
with exit 0 in 0.770 seconds. The readiness correction changed only tests and did
not change the artifact bytes. All seven hashes in both completed canonical
reports match these actual strict-built files. Each gate also verified that its
artifact inventory remained unchanged after package execution.

The separate strict command does not pass `--allow-divergent`. The canonical
working-tree distribution check deliberately passes that option to its test
build. Package results apply to the separate strict-built set only after all
seven hashes are compared and matched. That comparison passed for both current
runs. Their five real package phases were `cli-smoke`, `onboarding`,
`wheel-install`, `changelog` and `plugin-stdio`.

Reports `run-4bz0d9s7` (Python 3.11) and `run-ec3uiij0` (Python 3.12) identify
these complete working-tree distribution checks. Both report successful cleanup;
their in-run observers found the lock and workspace absent after return. Those
immediate observations do not resolve the separate historical cross-execution
filesystem visibility evidence below. Publication audits of this same candidate
are recorded next. The subsequent delivery delta comprises the manual Windows
workflow coverage extension, one compatibility wording correction in the packaged
[optional MCP documentation](../mcp.md#release-operations-and-their-effects), and
this report. That sentence identifies the executor, which is the bundled CLI in
installed plugin mode, as the version-gated component for Actions next/prepare;
it does not change the existing version threshold or runtime behavior. At `cc7c26e`, runtime
and test sources were still those qualified at `b2cec0f2`; the later D15/D16
revision has its own qualification boundary above.

Because `docs/mcp.md` is packaged, the wording correction changed the distributed
payload. The seven-file set below remains the initially qualified set. The
revised strict-built set and its actual five-phase provided-assets checks on both
Python versions are recorded above under `cc7c26e`; they establish that payload's
own package evidence. Workflow configuration and its bounded Linux provisioning
controls are recorded below.

### Initially qualified 0.32.0 artifact identities at b2cec0f2

| File | SHA-256 |
| --- | --- |
| `release-kit-plugin.zip` | `88ff50479cce33fe2bd6059689872a2acec61c4ee2193894e5618549db514a7f` |
| `release-kit-plugin.zip.sha256` | `23f1275ecad8c63b9a895bca75924e45c4be4b1183a3431385cf709bc903d81f` |
| `release.json` | `d92cff6e1532f7065ff01f5e563ec8228d585831c61ac2a3704508da40d49ddf` |
| `release_kit-0.32.0-py3-none-any.whl` | `8efb4ce6866307b606736d68013243e16d77cdf3ed8764affc78b9c855cb302b` |
| `release_kit-0.32.0-py3-none-any.whl.sha256` | `26d8c7aa1ef9f05e04fde26325fb762c3a7ad333c84b6c401516b9a2f02bd75c` |
| `relkit.pyz` | `909b0a9ac01ea76ca69faef58811cea9aec2effad272d4614bc69613fb3ffce7` |
| `relkit.pyz.sha256` | `17a188b0e8e3ea31ca8f32b8885f8d8cb341522a22a0321306c55d2c326cc8ef` |

This is the seven-file set qualified by both completed Linux runs at `b2cec0f2`.
It remains evidence for those exact bytes, preceding the packaged MCP wording
correction. The earlier qualified inventory below also remains associated with
its own source identity; its historical results are preserved separately.

### Packaged publication audits at b2cec0f2

The qualified `relkit.pyz` from the seven-file set above audited the same clean
`b2cec0f2fafccae396147583e75d399a508bb6dd` source and `8fa0bc5a` tree on native
Linux/Python 3.12.14. Both actual commands used `audit --strict --no-download
--json`; the second also selected `--history`. The worktree audit passed in
12.242 seconds and the history audit in 35.825 seconds. Each returned a valid
CLI-0 envelope, native Betterleaks 1.8.1 and Lychee 0.24.2 status 0, and no errors,
warnings, retained diagnostics or unconfirmed cleanup.

The recorded scope includes 22 advertised refs, 25 local refs and 117 commits
reachable from HEAD and the selected ref objects, including PR head and merge
refs. History selection covers branches, remotes, tags, and the configured pull-request,
merge-request, change and notes ref families. No advertised-ref mismatch was
observed before or after either audit. Source, refs, advertised refs, all seven
artifact hashes and the qualifying distribution report remained unchanged.

The public policy uses `.gitleaks.toml`, structural exclusion `tests/*`, no
baseline entries, candidate inspection enabled and owner mode disabled. Policy
and Betterleaks inspect selected history; Lychee operates offline against the
current Markdown snapshot and local links. These results do not claim checks of
every historical Markdown version, external HTTP availability or every hosted
surface. They qualify this exact candidate and scope. The later `cc7c26e`
workflow/documentation revision has its own strict inventory and provided-assets
qualification above. Final remote-revision publication audits are recorded
separately in the PR delivery evidence; the earlier audits remain bound to their
original source and ref identities.

### Earlier Python 3.11 source-fixture failure at 28eff54

Report `run-bvau4zhk` records the native Linux x86_64/Python 3.11.17 attempt at
`28eff54c07a8fd04bb9a78a051cf2a4c3e443bac`. The base suite ran 762 tests in
91.546 seconds with one error. The complete attempt returned exit 1 after
96.334 seconds; Ruff, MCP, the canonical build and all package phases were not
reached. Source identity and all refs remained unchanged, and the failed-run
workspace was retained. This failed attempt does not qualify the candidate or
its separate strict-built assets.

The error was in
`OwnedProcessTests.test_source_gate_preserves_failed_sequential_worker_cleanup`.
The real outer command returned `TimeoutExpired` at its existing two-second
deadline where the test expected `CleanupError`. The PID readiness assertion
appeared after that expected-exception block, so it was never reached. The fixture
had already performed cleanup when inspected; whether its worker was ready at
the original deadline is unknown. The observed exception does not establish a
surviving worker or a product cleanup defect.

This sequential fixture was added during the review, whereas the earlier MCP
readiness cases in D13 existed in the baseline. The shared missing readiness
precondition is tracked under D13 without adding another product finding. The
synchronous test helper now waits for complete atomic markers before forwarding
the first real `Popen.communicate` with its unchanged timeout arguments. Startup
has its own ten-second assertion bound; cleanup reentry does not repeat that wait.
A successful-readiness postcondition prevents an expected cleanup refusal from
consuming a failed startup assertion. Both the outer and inner timeout controls
retain their original operation budgets and ownership checks.

An independent actual-child control added the same three-second startup delay to
the generated checker. With the old fixture, SIGTERM arrived before the worker
was ready, and the operation returned `TimeoutExpired` after 2.355 seconds. With
the corrected fixture, the worker was ready before SIGTERM; the same two-second
operation budget returned the expected `CleanupError` after 9.209 seconds, with
the lock retained and the source-check report marked unconfirmed. Product source
was unchanged. This reproduces a startup-sensitive fixture failure and validates
the correction; it does not recover the original gate's missing readiness state.
A separate real never-ready child with an injected cleanup refusal demonstrated
that omitting the readiness postcondition falsely satisfied the expected error,
while the maintained helper refused startup.

The synchronous family passed on Python 3.11 and 3.12, running 21 tests including
two explicit skips in 43.499 and 49.023 seconds respectively. Those skips were the
Windows cwd control and this host's refused AF_UNIX capability probe. The packets
preceded one final application of the same helper to the generated relay's inner
two-second timeout; that actual method then passed on both interpreters in 2.995
and 3.451 seconds, with `env={}` and the outer eight-second timeout unchanged.
Independent source review accepted the final test-only diff, published in
`b2cec0f2fafccae396147583e75d399a508bb6dd`. Complete qualification of that exact
source is recorded above; the earlier failed gate receipt remains a failed
acceptance result.

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
| Separate strict release build | Qualified | Seven-file inventory below, built from the candidate commit and matched to the canonical working-tree test-build inventory |
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

### Earlier qualified artifact identities

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
follow that code commit. The final integrated candidate then passed complete
Linux Python 3.11 and Python 3.12 qualification at `b2cec0f2`, as recorded above.
Historical passes and hashes remain attached to the source that actually ran
them; the earlier failed attempts are preserved as failures.

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

A later observation also found the previously qualified `cc7c26e` Python 3.12
run `run-olpxorvn` and its lock visible again at 12:19:07.927 UTC, although
the actual driver had recorded both absent immediately after completion at
11:37:20.901 UTC. Their observed change times were later than that completion;
no cleanup or mutation was performed during the observation. This extends the
same unresolved visibility boundary without establishing an actor or mechanism.
The historical package pass and immediate absence are preserved as such, not
as proof that the paths stayed absent indefinitely. No historical process
creation trace identifies who recreated the paths, and available host
observability did not provide that attribution; this does not assert that
every possible tracing technique is unavailable.

For the later `1428dbb0` canonical run `run-3muaddpu`, the immediate
post-return observer again recorded the lock and workspace absent at
13:12:16 UTC. At 13:15:07.995 UTC they and their owned phase directories were
visible with later change times. A scoped read-only process check found no
matching cwd or command line among processes visible through `/proc`; this
does not establish an actor or mechanism.
The observed state was preserved and the actual eight-phase pass is unchanged.
The later `b10a0708` checkpoint above adds original in-progress inode records,
immediate absence and subsequently visible different identities for that run;
the responsible actor and mechanism remain unknown.

An ordinary filesystem negative control made 207 post-deletion observations,
including four independent stat executions, through 99.515 seconds; the deleted
paths remained absent. This control did not reproduce the later visibility and does not prove its cause. The
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
bytes. The `b2cec0f2` candidate's separate packaged publication results are
recorded above.

### Native tool and platform coverage

Native tool controls use Betterleaks 1.8.1, Lychee 0.24.2 and git-cliff 2.14.1.
The actual scanner matrix distinguishes malformed configuration (CLI 2), completed
findings (CLI 1) and clean completion (CLI 0). The packaged changelog smoke checks
initial stable and preview notes, preview progression, stable notes across earlier
previews, exact type matching, breaking changes, translated headings, refusal of
source overwrite, and suppression of template output side effects. It verifies
that HEAD and tags remain unchanged. Separate real-Git controls exercise ambiguous
tag boundaries.

Native execution evidence includes the earlier complete Linux x86_64 checks on
Python 3.12.14 and provisioned Python 3.11.17 at
`b2cec0f2fafccae396147583e75d399a508bb6dd`, plus both five-phase
provided-assets checks of the revised `cc7c26e` payload. The failed 3.11
attempts and historical successful 3.12 gate remain distinguished above.
Authenticated hosted workflow #5 later exercised native Linux, macOS and
Windows source jobs at `93718d0c`: Linux passed, while the macOS and Windows
base results failed as recorded in D15/D16. Run #6 at `1428dbb0` then passed
all three native source jobs and the Linux/macOS package jobs, but failed its
Windows package changelog phase. Run #7 at `f36bb635` again passed all three
source jobs and the Linux/macOS package jobs, and passed Windows changelog;
its subsequent Windows plugin dependency installation failure still prevents
an all-platform package pass. Neither run contains the later D17/D18 corrections.
A mocked platform boundary or skipped conditional test is not native acceptance.

The existing [manual release workflow](../../.github/workflows/release.yml)
declares three Python 3.11 source jobs, one shared Linux candidate build and
three package jobs consuming the same downloaded candidate. Run #5 did not
reach that candidate build or expand the package matrix because source
verification failed. Runs #6 and #7 each built one shared candidate and reached
all three package jobs, with their distinct Windows failures recorded above.
No real hosted release,
installed-user update, user hook,
production rollback, client registration or native desktop-client discovery
was performed. Those operations cannot be inferred from local/SDK fixtures or
a source-only hosted result. Any new dispatch must identify the reviewed
remote commit and checked bytes; the workflow itself does not publish.

D14 closes a separate configuration gap in that workflow. The existing
`test_update_with_real_engines_without_windows_processor_environment` requires
native Windows, `RELKIT_TEST_REAL_ENGINES=1`, and verified executable/archive
pairs for all three maintained tools in the source root's `.cache/release-kit`
directory. The earlier workflow supplied neither the opt-in nor the archives;
its ordinary Windows package checks did not exercise this specific MCP sync
apply/audit/rollback scenario with `PROCESSOR_*` removed.

The applied workflow now runs a Windows-only inline Python provisioning step
through `storage.inside` and the existing pinned resolver, then sets the opt-in
to `1` for the Windows source check and `0` elsewhere. Its SHA-256 is
`ba589b7602ae54d1f067c8a859ea858e86761ef4e37f71ce6df43eb341f1168d`.
Independent source review checked the exact cache consumer, environment
propagation and unmodified test assertions. YAML parsing, Python compilation and
patch validation passed. The manual seven-job structure, action pins, permissions
and same-candidate build/package jobs are unchanged.

The exact inline program was also executed on Linux/Python 3.12.14 using seeded,
verified Betterleaks, Lychee and git-cliff archives, with no executables present
initially. All three extracted executable hashes matched their pins. A separate
consumer resolved every engine from the default cache with downloads disabled
and no cache override; the actual distribution environment retained the opt-in.
Corrupt-archive and aliased-cache controls refused execution, respectively leaving
no executable and leaving the aliased target unchanged. That control is real Linux cache
and control-flow evidence without new downloads or native Windows execution.
Separately, hosted run #5 completed native Windows provisioning for all three
tools, but stopped in its base suite before MCP. Runs #6 and #7 then passed
their native Windows source gates and explicitly logged the named real-engine
MCP test as successful; that source acceptance is distinct from their later
failed package phases.

The earlier signed-out browser limitation was resolved. Authenticated manual
dispatch actually ran the jobs recorded above. Run #5's source failures and
the distinct Windows package failures in runs #6 and #7 remain preserved.
Native source qualification and the named Windows MCP acceptance have passed
for those recorded revisions. Complete Windows package acceptance and native
qualification of the later D17/D18 payload remain open; native desktop-client
discovery is an unverified scope boundary.

A read-only preflight resolved all four exact upstream action commits in the
workflow and verified each `action.yml` Git blob against its pin. All declare the
`node24` runtime; the pinned
[setup-python README](https://github.com/actions/setup-python/blob/5fda3b95a4ea91299a34e894583c3862153e4b97/README.md)
requires Actions Runner 2.327.1 or later. None declares a Docker-only action
runtime. This preflight verifies external pins and runtime configuration; actual hosted
execution is separately recorded above.

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
host refusal is not native forkserver acceptance. Hosted Linux run #5 provides
separate source-verified execution evidence for the explicit forkserver oracle,
with the aggregate-skip inference qualified above. The macOS run #5 failed log
could not establish the same result. The complete Linux and macOS base passes
in runs #6 and #7 identify every skipped test, exclude this oracle from those
skips, and supply the same source-verified native forkserver execution evidence.

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

## PR follow-up — 2026-10-10

Review of the integrated 0.32.0 changes at commit
`1875add631b013be8e4d0309073c4f2ca8673ea2` found two additional history-audit
issues. These findings supplement the original 42 items above.

| ID | Severity and origin | Observed behavior | Correction and regression boundary |
| --- | --- | --- | --- |
| F1 | Medium; integration regression | A public tree or notes ref containing a missing ordinary blob returned a clean history verdict because a short `cat-file --batch-check` record was ignored. | Reconcile every requested ordinary object by count, identity, uniqueness, type and size; reject missing or malformed records and payload-size disagreement. Native missing-blob and valid direct-tree alias controls distinguish corruption from supported refs. Gitlink-only commit IDs are excluded from the blob inventory and retain the existing `external-repository` finding even when the donor commit is absent locally. |
| F2 | High; baseline defect | Git message recoding through `i18n.logOutputEncoding=ISO-8859-1` replaced a declared Unicode owner marker during UTF-8 decoding and produced a clean history verdict. Valid legacy-encoded commits exposed the same bypass. | Request UTF-8 through Git's native `log --encoding=UTF-8`, decode message inventory strictly and report a controlled failure when decoding is impossible. Native UTF-8 and legacy-encoding controls retain the Unicode marker; ASCII controls still pass, and contributor-attribution exemptions continue to leave owner privacy mandatory. |

The audit module passed 98 tests with two platform skips. Independent focused
verification passed the missing-blob/direct-tree and both gitlink controls. The
new donor/superproject fixture requires `git fsck --strict` to succeed, so a
valid external submodule cannot be mistaken for corrupt publishable content.
Ruff lint, Ruff format checks and the patch whitespace check passed.

The complete source gate then passed on Windows / CPython 3.11.9 with the
follow-up changes: 785 base tests in 215.906 seconds with 20 named platform
skips, Ruff lint and format checks, and 78 explicit locked MCP tests in
255.763 seconds with six named POSIX skips. Real-engine controls were enabled.
The gate removed its owned run workspace and recorded both source stages as
passed. Candidate-byte, other-platform and native desktop-client qualification
remain separate gates.

An initial source run used a deeper owned temporary directory and failed two
plugin launcher fixtures before their intended mocked operations. Its plugin
roots exceeded the documented 165 UTF-16-unit Windows installer bound. Both
controls passed in a shorter owned directory, followed by the complete passing
gate above. This was a qualification-path precondition; the launcher guard and
the test assertions were preserved.

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
