# Changelog

## [Unreleased]

## [0.26.1](https://github.com/Muratovnik/release-kit/compare/v0.26.0...v0.26.1) (2026-09-15)

### Highlights

0.25.0 renamed the `vue-like` changelog profile and refused the old name, which closed
this tool's own upgrade path: the installed CLI could not read the new name, so a project
could not fix its configuration first, and the candidate could not read the old one, so
`update` failed its post-update audit and rolled itself back. Any project still naming the
former profile was held between two releases. The name is accepted again and mapped to
`conventional-changelog`, which is what it always meant; `audit` reports it and names the
current one.

The documentation is now also published in Russian and Simplified Chinese, covering
install, first check, audit policy and updates. English remains canonical.

### Bug Fixes

- **changelog:** accept the former profile name instead of stranding its users ([0aa7684](https://github.com/Muratovnik/release-kit/commit/0aa768424a2f5e4ce292fa94bac290db9d658b26))

## [0.26.0](https://github.com/Muratovnik/release-kit/compare/v0.25.0...v0.26.0) (2026-09-14)

### Highlights

A release used to print four lines in ten minutes, so a slow check and a hung one
looked the same. Stages are now numbered against the sequence the run performs, and a
long step reports how long it has been running. The indicator writes only to a
terminal and only to the process's original stderr, so captured logs and CI output
keep the shape they had.

Two publications of this project were lost to a draft that its own run could not find
again: creation reported nothing addressable, so the run searched a list that had not
caught up. Creation now returns the release and it is addressed by that id.

### Features

- **release:** say which stage is running and for how long ([c22d53c](https://github.com/Muratovnik/release-kit/commit/c22d53c9e4a629d52940010959b87f2a4b616c61))

### Bug Fixes

- **check:** remove a successful check run whose child tools filled its tmp ([c916b38](https://github.com/Muratovnik/release-kit/commit/c916b38563c303adb83159b4bea238e302d46240))
- **release:** address the created draft by the id creation returned ([07e9fd7](https://github.com/Muratovnik/release-kit/commit/07e9fd79b7e88d7f89429430e4b430b173725448))

## [0.25.0](https://github.com/Muratovnik/release-kit/compare/v0.24.0...v0.25.0) (2026-09-14)

### Highlights

A project can now declare how its changelog entries are drafted, and `relkit notes
<version> --draft` produces one from history. `engine = "git-cliff"` is provisioned and
verified the way the scanners are; `command = [...]` runs an exact argv the project
supplies, which is how the Node tools are reached. A draft is checked against the
configured profile and written nowhere: publication still reads the entry a person
reviewed and committed.

### BREAKING CHANGES

The `vue-like` changelog profile is now `conventional-changelog`. It named a project
that publishes the layout rather than the layout itself, which is what
`conventional-changelog -p angular` emits. The old value is refused and the error names
the new one; there is no alias.

### Features

- **changelog:** draft entries with a declared generator ([6cefa01](https://github.com/Muratovnik/release-kit/commit/6cefa011698fa79cf13b7cf865bced245d77bbf4))

## [0.24.0](https://github.com/Muratovnik/release-kit/compare/v0.23.2...v0.24.0) (2026-09-14)

### Features

- **build:** publish an installable wheel beside the zipapp ([95964ec](https://github.com/Muratovnik/release-kit/commit/95964ec9ead2d8e7e74707c343e4f72b6070e19b))

### Bug Fixes

- **storage:** stop keeping every run's temporary workspace forever ([a838876](https://github.com/Muratovnik/release-kit/commit/a83887615945ce4c5005b07802ef19549de6394e))
- **storage:** clear the read-only bit Git leaves on objects before removing them ([b8d2306](https://github.com/Muratovnik/release-kit/commit/b8d2306115715bc1cb9d754d6f3c7597d3a371ba))
## [0.23.2](https://github.com/Muratovnik/release-kit/compare/v0.23.1...v0.23.2) (2026-09-14)

### Bug Fixes

- **storage:** retry a receipt rename Windows briefly refuses ([5ae486d](https://github.com/Muratovnik/release-kit/commit/5ae486dbf0c0dcebfcdfbe1af651a207ecd4701a))
- **tools:** resolve the plugin lock online and guard resolved versions ([e207222](https://github.com/Muratovnik/release-kit/commit/e207222162a5df9b47262ef273513c40b3674799))

## [0.23.1](https://github.com/Muratovnik/release-kit/compare/v0.23.0...v0.23.1) (2026-09-14)

### Performance Improvements

- **exposure:** normalize each scanned text once per file ([c31c99a](https://github.com/Muratovnik/release-kit/commit/c31c99aed390d7e5cd0107f3f0646af037366fcf))
- **engines:** pin executable digests and provision both engines at once ([a3d7370](https://github.com/Muratovnik/release-kit/commit/a3d7370d4595a9ed76d48321c37ed0a9aeb139e2))

## [0.23.0](https://github.com/Muratovnik/release-kit/compare/v0.21.1...v0.23.0) (2026-09-14)

### Features

- **checks:** qualify exact distributions and ship focused user guides ([090dec6](https://github.com/Muratovnik/release-kit/commit/090dec6de57df194981e65deaebf9708db1d6fb2))
- **owner:** select the policy root from repository-local Git configuration ([3f76258](https://github.com/Muratovnik/release-kit/commit/3f762589351d284dba10cdc970c4aa6d7a0d85b6))

### Bug Fixes

- **mcp:** bind projects using local release publishers ([cde382b](https://github.com/Muratovnik/release-kit/commit/cde382b4b133ade24d4536dfc88db88bc92d8311))
- **overlay:** parse complete Dotbot manifests with optional YAML support ([1f064c6](https://github.com/Muratovnik/release-kit/commit/1f064c6191bed11fbf0566277ed8be92df9f07d4))
- **release:** retain recovery state until owned command cleanup is confirmed ([43660e7](https://github.com/Muratovnik/release-kit/commit/43660e73f03808287dabe43d5332b5c62791eded))
- **checks:** allow bounded full source suites to finish ([a7f436d](https://github.com/Muratovnik/release-kit/commit/a7f436d589c17b3d8bd3b0aaf9f1d3de1f12876b))
- **plugin:** serialize cold startup and support long Windows paths ([7602699](https://github.com/Muratovnik/release-kit/commit/760269966529105fa6c9080c9aabc25480f67070))
- **plugin:** load compiled dependencies from deeply installed runtimes ([fb862d5](https://github.com/Muratovnik/release-kit/commit/fb862d545e9795636de76bebabe3661226bf2298))

## [0.21.1](https://github.com/Muratovnik/release-kit/compare/v0.18.0...v0.21.1) (2026-09-12)

### Features

- **release:** verify published artifacts and align plugin versions ([db67e08](https://github.com/Muratovnik/release-kit/commit/db67e08b6883d141d4775f5f1074d27689f5e6b9))
- **release:** prepare tagless candidates against published versions ([e871ae3](https://github.com/Muratovnik/release-kit/commit/e871ae3793446910a0a1972bbcceb96a5b512aae))
- **release:** prepare locally and make hosting an optional adapter ([ad1d345](https://github.com/Muratovnik/release-kit/commit/ad1d345fa7f3009c29ad3786dec2f2b1b0128ee2))

### Bug Fixes

- **release:** accept a required CI job that GitHub reports as a matrix ([77a3124](https://github.com/Muratovnik/release-kit/commit/77a31249c050539391b003206da3c625bd86acc0))
- **build:** make the projection a function of the commit, not of the host ([c0b5ab0](https://github.com/Muratovnik/release-kit/commit/c0b5ab0f45242d26b3e37fd7c2523553531c59e6))
- **sync:** migrate guards with the installed verifier ([8d42c07](https://github.com/Muratovnik/release-kit/commit/8d42c0786c1cae5e3b9d4c59ff5232dec4e749a2))
- **plugin:** include portable release guides in the distributed package ([2591a14](https://github.com/Muratovnik/release-kit/commit/2591a14c3136c127e2cc6dd4d634e3ccf46d6a58))
- **release:** resolve unpublished GitHub drafts through the releases list ([5846bd9](https://github.com/Muratovnik/release-kit/commit/5846bd93fb160cde2f28d8e64cb7cc0a8ff0a409))

## [0.18.0](https://github.com/Muratovnik/release-kit/compare/v0.16.0...v0.18.0) (2026-09-07)

### Features

- **update:** prune unrestorable backups and stop leaving scratch behind ([1232610](https://github.com/Muratovnik/release-kit/commit/123261086ac8dc31fd20ae7b81591d6609f40c87))
- **release:** let the adopter declare whether provenance can be verified ([6a485c9](https://github.com/Muratovnik/release-kit/commit/6a485c908201b600892c2c16b6162b54a2730d95))

### Bug Fixes

- **update:** adopt an older guard template instead of reporting one ([dcf8519](https://github.com/Muratovnik/release-kit/commit/dcf85190e957455a6dafa45814e328016aa0a2c6))
- **release:** read a receipt written before provenance was declarable ([78d79d4](https://github.com/Muratovnik/release-kit/commit/78d79d46a5fc9b35adcdfe2d3d6081b1db932f02))

## [0.16.0](https://github.com/Muratovnik/release-kit/compare/v0.6.0...v0.16.0) (2026-09-07)

### Features

- **release:** add resumable publication in 0.7.0 ([53cf1b4](https://github.com/Muratovnik/release-kit/commit/53cf1b4436c59694e4438c7e6f6a2ac533df241c))
- **cli:** add structured results in 0.8.0 ([ca1fb9d](https://github.com/Muratovnik/release-kit/commit/ca1fb9dba300fe203d7a75902e7ca7b97970e549))
- **mcp:** expose full release workflows in 0.9.0 ([2dd686d](https://github.com/Muratovnik/release-kit/commit/2dd686d8a1f3c156675dec926db69ec05802503b))
- **plugin:** bundle skill and multi-project MCP in 0.10.0 ([9c79e9c](https://github.com/Muratovnik/release-kit/commit/9c79e9c2f8363ebb349805ec3938f61c9f8bc665))
- **plugin:** synchronize bundled CLI updates in 0.11.0 ([13ae5bf](https://github.com/Muratovnik/release-kit/commit/13ae5bfe7857926b5c50ea61fe1574ebd3dfdee9))
- **plugin:** honor scoped update authorization in 0.12.0 ([29e291e](https://github.com/Muratovnik/release-kit/commit/29e291e739aff932843793dade3a7f479bb5acd9))
- **mcp:** honor scoped release authorization in 0.13.0 ([6758ebb](https://github.com/Muratovnik/release-kit/commit/6758ebb89e055df3a2cce73e0c92b72c1e64e037))
- **release:** close field-report gaps in 0.14.0 ([23cc059](https://github.com/Muratovnik/release-kit/commit/23cc0596f226884c46eba611881feec3b05155ff))
- **release:** decide the published asset set by GitHub's signed attestation ([6e28845](https://github.com/Muratovnik/release-kit/commit/6e288456af9902e092291052db4f2ac398971dc9))

### Bug Fixes

- **release:** preserve bracketed source paths in 0.7.1 ([d620224](https://github.com/Muratovnik/release-kit/commit/d620224d30beb3d54e1225a26a1262f50423a7ca))
- **toolchain:** detect Windows architecture without processor environment ([e52f5ab](https://github.com/Muratovnik/release-kit/commit/e52f5abda4dceaed5d453c7c57fe9a6c76e47061))
- **notes:** keep commit evidence inline ([48aea43](https://github.com/Muratovnik/release-kit/commit/48aea4348d76faabeba61840399be7ce81451a6c))
- close publication audit gaps in 0.13.2 ([7880d32](https://github.com/Muratovnik/release-kit/commit/7880d32e31cd223c7c2331e8d519242fb2cc54d1))
- close the review findings in 0.15.0 ([a950946](https://github.com/Muratovnik/release-kit/commit/a950946d04afc19ad198c6092a3b058fd218b008))

## [0.6.0] (2026-09-01)

### BREAKING CHANGES

- enforce private owner publication policy ([63e6347](https://github.com/Muratovnik/release-kit/commit/63e634749d1a002accc32eb93101475ca6eff923))
- **protection:** separate dispatcher ownership ([704e254](https://github.com/Muratovnik/release-kit/commit/704e2540c30f5c5096411ce69a6aa8fad32edea4))

### Features

- two gates a repository can adopt without adopting a workspace ([aec2977](https://github.com/Muratovnik/release-kit/commit/aec29776448a2a40c4241960ec2723577a010a11))
- **exposure:** declare a surface private instead of judging it file by file ([1c30fa5](https://github.com/Muratovnik/release-kit/commit/1c30fa55d12bf7e912ecdf545d5e12dce509ff90))
- **overlay:** verify the links, and leave the linking to dotbot ([a5fd017](https://github.com/Muratovnik/release-kit/commit/a5fd017e1ebc295bccdb6b5a405809ab560ea136))
- unify publication auditing ([2ed589b](https://github.com/Muratovnik/release-kit/commit/2ed589b4b4813dfcdfa9397d4bf91e957f6fe814))
- add semantic publication policy ([793380d](https://github.com/Muratovnik/release-kit/commit/793380df7fe7830706418dd28f2ef072fbeacc3d))
- **notes:** validate opt-in curated changelog profiles ([ea8a277](https://github.com/Muratovnik/release-kit/commit/ea8a277b7c9b4888d53506dba0e5b8bb61e01e1c))
- **update:** add safe project upgrades in 0.6.0 ([d7f9ec5](https://github.com/Muratovnik/release-kit/commit/d7f9ec50447a6bcbd4e538933ab97a918a8a55a9))

### Bug Fixes

- exclude worktree deletions from publication input ([367ed67](https://github.com/Muratovnik/release-kit/commit/367ed67d882c0f55b294e2c828fabe5b9d783f14))
- scope commit-message audit to privacy ([5a9f530](https://github.com/Muratovnik/release-kit/commit/5a9f5300de9056bb6c9eccdce34197ad368ca6ab))
- detect wrapped private values ([12eb867](https://github.com/Muratovnik/release-kit/commit/12eb867216ebbb4b18b3311c1b67f821d7561c3b))
- harden publication privacy boundaries ([49f3840](https://github.com/Muratovnik/release-kit/commit/49f3840fc1ae58ed468218a42ceab6ea99adcfdb))

### Performance Improvements

- batch wrapped history scan ([4be5430](https://github.com/Muratovnik/release-kit/commit/4be5430795f203b154d12a183e01ce13bbe37fc1))
