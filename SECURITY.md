# Security policy

## Intended use
PDK is for **development gateways only**. Its gateway module is unsigned and writes to a sandbox project; installing it on a production gateway is unsupported and unsafe.

What the module is designed to guarantee:
- It answers only loopback connections, and every request needs a token that changes on every gateway start.
- Without `-Dpdk.sandbox` it is read-only. With it, it writes only to that project, and to the optional `-Dpdk.target` project only finished work: views, style classes and project scripts outside `PDK/` and the project stylesheet (through promote), plus the eyes' preview pages under `/pdk/` in its page configuration.
- Tags are written only under `[default]PDK/`.
- Every write carries the expected resource signature, so concurrent edits are never overwritten.

A way around any of these is a vulnerability.

## Reporting a vulnerability
Please report it privately to the author through GitHub ([@fcastel2002](https://github.com/fcastel2002)). Do not open a public issue.

Include the PDK commit, Ignition and Perspective versions, and the steps to reproduce. You will get an answer within a week.

## Supported versions
None. PDK 0.5.0 is the final community edition: the repository is archived and gets no more fixes, security fixes included. A reported vulnerability is fixed in the maintained (commercial) edition.
