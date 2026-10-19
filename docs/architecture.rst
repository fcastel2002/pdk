Architecture
============

This chapter describes how PDK is built and why: its components, how a request flows through them, the security
model and the main design decisions.

Components
----------

PDK has a Python package that holds all the logic, a thin Java module that runs inside the gateway, and the
integrations that expose them to agents.

Python package (``pdk/``)
~~~~~~~~~~~~~~~~~~~~~~~~~

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Module
     - Responsibility
   * - ``bundle``
     - Extracts the schema bundle from the installed Perspective module (``.modl``) and loads it.
   * - ``validate/``
     - The offline validator: structure, schemas, ``propConfig`` and events, code (expressions and scripts),
       references and design lint.
   * - ``catalog``
     - Compact answers about components, props, bindings and events from the bundle.
   * - ``diagnostics``
     - The table of stable codes and the budgeted, focusable rendering shared by every report.
   * - ``sync``, ``stylesheet``
     - Pull, deploy, promote and delete of views and of the stylesheet, with signatures and conflict diffs.
   * - ``eyes/``
     - The browser harness (``browser``), the join of ``view.json``, DOM and gateway tree (``join``), the anomaly
       rules (``rules``), measurements (``measure``), expectations (``expect``) and actions (``act``).
   * - ``tags``
     - Simulated memory tags.
   * - ``gateway/``
     - The HTTP client of the module (``client``) and the build and install of the module (``module``).
   * - ``live``
     - The live catalog of the gateway, compared with the bundle.
   * - ``cli``, ``mcp_server``, ``hook``
     - The three entry points: command line, MCP server and Claude Code hook.
   * - ``doctor``, ``config``
     - Environment checks and configuration.

Gateway module (``gateway/``)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

A Java 17 module of about fifteen classes that mounts a REST API under ``/data/pdk/v1`` (see
:doc:`reference/rest-api`). It reads and writes project resources with Ignition's own resource API, exposes the
evaluated component tree of open Perspective sessions, taps the gateway log, and manages memory tags under
``PDK/``. It is compiled with ``javac`` against the jars of the local Ignition install, so it always matches the
installed version, and packaged as an unsigned ``.modl``.

Integrations
~~~~~~~~~~~~

The ``pdk`` command line, the ``pdk-mcp`` MCP server, and the Claude Code configuration in ``.claude/`` (hook, skill
and subagents) described in :doc:`guide/agents`.

How a deploy works
------------------

.. mermaid::

   sequenceDiagram
     participant A as Agent / CLI
     participant P as pdk (Python)
     participant M as PDK module
     participant G as Gateway resources
     A->>P: deploy work/pdk_sandbox/PDK/Pump
     P->>P: validate view.json (offline)
     P->>M: PUT /projects/pdk_sandbox/resources/…/PDK/Pump<br/>files + expected signature + token
     M->>M: loopback? token? inside the sandbox?
     M->>G: push with the expected signature
     alt signature matches
       G-->>M: written, new signature
       M-->>P: 200 / 201
       P->>M: add preview page /pdk/PDK/Pump (signed)
       P-->>A: created … preview: http://…
     else changed since the pull
       G-->>M: signature mismatch
       M-->>P: 409 + current version
       P-->>A: G008 + diff, view.gateway.json saved
     end

How an inspection works
-----------------------

1. PDK reads the deployed ``view.json`` from the gateway and the current position of the gateway log.
2. Chromium opens the preview page and waits until the root is visible and the layout stops moving.
3. If actions were given, they run in order, each followed by a settle.
4. PDK extracts every component's box, text, style and scrolling from the DOM, reads the gateway's evaluated tree
   for that page (binding qualities) and the log events since step 1.
5. The three are joined by the component's position in the tree: the pointer ``/root/children/1/children/0``, the
   gateway address ``0:1:0`` and the DOM path ``C.0:1:0`` designate the same component.
6. The anomaly rules and the expectations run over the joined model, and the report is rendered within the token
   budget.

Security model
--------------

PDK assumes a development gateway on the same machine as the person or agent using it. The module enforces:

Loopback only
   Every request must come from and arrive at a loopback address (``127.0.0.0/8`` or ``::1``), without proxy
   forwarding headers. A request from the network is refused.
A token per start
   The module writes a random 256-bit token to ``<ignition>/data/pdk-gateway.token`` on every start; every request
   must carry it in the ``X-PDK-Token`` header. The file is written privately and then moved over the old one, so
   it is never readable by other local users; on Linux and macOS it is ``rw-r-----``, so members of the gateway's
   group can use PDK.
Read-only by default
   Without ``-Dpdk.sandbox`` nothing can be written. With it, resources can be written only in the sandbox, and in
   the target project only finished work (views, style classes and project scripts outside ``PDK/``, and the
   stylesheet) through promote, plus the eyes' preview pages under ``/pdk/``. Projects and tags are named in
   ``ignition.conf``, which needs administrator rights, not by the client.
Tags only under ``PDK/``
   UDT definitions only under ``_types_/PDK/``; synthetic history only for tags under ``PDK/``. Tag imports accept
   only folders, memory tags, UDT definitions whose members are memory tags and instances of them (no OPC,
   references, expressions, scripts, bindings other than to UDT parameters or alarm
   pipelines), and writes only reach ``[provider]PDK/``.
No lost edits
   Every write and delete carries the expected signature, so a concurrent edit is reported, never overwritten.

These measures protect against accidents and against other machines on the network; they are not a sandbox
against a malicious local user, who could equally edit the gateway's files. That is why PDK must not be installed
on production gateways. To report a vulnerability, see the repository's ``SECURITY.md``.

Design decisions
----------------

Logic in Python, a thin module
   Ignition 8.3 does not reload a module without restarting the gateway, so every change to the module is
   expensive. The module only exposes what Python cannot reach; rules, diffs and reports live in Python.

Exact schemas from the local install
   Validation uses the schemas Perspective ships with the installed version, extracted locally, rather than a
   hand-written or distributed copy. This makes the validator exact for each version and keeps Inductive Automation
   content out of the repository.

Anomalies, not dumps
   Reports list only problems, ordered by severity and position, within about 1,500 tokens, with ``focus`` for the
   detail. An agent's context is its working memory; filling it with what is fine hides what is wrong.

Stable codes, pointers and hints
   Every diagnostic, from the validator, the gateway or the eyes, has a code that never changes meaning, an RFC 6901
   pointer into the file, and a hint. Agents and people can act on it without interpretation.

Warn on what cannot be validated
   When PDK cannot decide something (an expression its parser does not cover, a component from an unknown
   module), it reports a warning instead of blocking.

Signed writes
   The Designer and the agent can work on the same project; optimistic concurrency with signatures makes conflicts
   explicit and recoverable.

Quality assurance
-----------------

- A pytest suite covers the validator, the eyes' rules on recorded pages, the gateway client against a fake
  module, the build of the module and its self-test, and the CLI, MCP and hook surfaces. Tests that need a live
  gateway are marked ``gateway`` and write only under ``PDK/_selftest/``.
- ``bench/mutations.py`` breaks the test views in known ways and checks that each mutation is caught with a pointer
  and a hint (the validator's *mutation score*).
- ``bench/briefs/`` holds seven realistic screen requests with acceptance criteria, used to measure how well an
  agent builds screens with PDK.
