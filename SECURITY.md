# Security policy

## Intended use
PDK is for **development gateways only**. Its gateway module is unsigned and writes to a sandbox project; installing it on a production gateway is unsupported and unsafe.

What the module is designed to guarantee:
- It answers only loopback connections, and every request needs a token that changes on every gateway start.
- Without `-Dpdk.sandbox` it is read-only. With it, it writes only to that project, and to the optional `-Dpdk.target` project only views outside `PDK/` and the stylesheet.
- Tags are written only under `[default]PDK/`.
- Every write carries the expected resource signature, so concurrent edits are never overwritten.

A way around any of these is a vulnerability.

## Reporting a vulnerability
Please report it privately through GitHub: in this repository, open **Security → Report a vulnerability** ([private vulnerability reporting](https://docs.github.com/en/code-security/security-advisories/guidance-on-reporting-and-writing-information-about-vulnerabilities/privately-reporting-a-security-vulnerability)). Do not open a public issue.

Include the PDK commit, Ignition and Perspective versions, and the steps to reproduce. You will get an answer within a week. Fixes are released on `main` and announced in the advisory once users have had time to update.

## Supported versions
Only the latest commit on `main` gets security fixes.
