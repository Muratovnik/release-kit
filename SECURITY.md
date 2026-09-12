# Security reports

Do not report suspected credentials, private owner values, exploitable trust-boundary
failures or unredacted diagnostics in public issues or pull requests.

Send a private report to **el.muratovnik@gmail.com**, the maintainer address in this
project's package metadata. If GitHub's private vulnerability reporting is enabled
for this repository, that private channel is also suitable; do not assume it is
available or create a public issue when it is not.

Include the release-kit version, affected command/adapter, platform, a synthetic
reproducer and the expected versus observed behavior. State whether the issue
requires a malicious project, a compromised trusted publisher, local concurrent
writers or only ordinary user actions. Redact tokens, private values, local paths
and personal data. Do not send live credentials; a minimally redacted example is
usually sufficient to establish the issue.

A report is not permission to test against another user's project, publish a
release, change repository visibility or delete history. Coordinate any live
reproduction with the maintainer first. No response-time or support-lifetime SLA
is promised by this document.

Hash pinning and package inventories detect changes relative to approved inputs;
they do not make initially malicious code safe. Project commands are trusted code,
not an operating-system sandbox. See the [MCP trust boundary](docs/mcp.md) and
[distribution integrity](docs/distribution.md#integrity-and-trust).

Before a previously private repository or artifact is made public, follow the
[publication review](docs/publication-review.md). A clean current tree does not
establish that historical releases, hosted logs or attachments are safe to disclose.
