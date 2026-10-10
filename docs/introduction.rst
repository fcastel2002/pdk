Introduction
============

This chapter explains the problem PDK solves, the Ignition concepts it builds on, and the parts that make it up.
No prior experience with agents is assumed; some familiarity with Ignition helps but is not required.

The problem
-----------

Ignition Perspective screens are stored as JSON documents (``view.json``). Large language models can write that
JSON, but on their own they make characteristic mistakes:

- **Invented or misspelled properties.** Perspective silently ignores a property it does not know, so a typo such
  as ``justifyContent`` instead of ``justify`` produces no error, only a screen that looks wrong.
- **Mixed versions.** Components and properties change between Perspective releases; a model trained on several
  versions mixes them.
- **Broken bindings and scripts.** An expression using ``and`` instead of ``&&``, or a script without its leading
  tab, fails only at runtime.
- **Layouts that are valid but wrong.** Clipped text, a table collapsed to zero height or a header that does not
  span the screen are only visible when the view is rendered.

The usual answer is for a person to open each generated screen in the Designer and check it by hand. PDK replaces
most of that manual check with automatic verification, so the agent can find and fix its own mistakes before a
person looks at the result.

Ignition concepts
-----------------

**Ignition** is an industrial application platform by Inductive Automation. The parts PDK works with are:

Gateway
   The Ignition server. It runs projects, holds tags and serves Perspective sessions to browsers. PDK works with a
   *development* gateway installed on the same machine.

Project
   A container of resources (views, scripts, styles) on the gateway.

Perspective
   Ignition's web-based visualization module. Its screens are built from *views*.

View and ``view.json``
   A screen or a reusable piece of a screen. It is a tree of *components* (containers, labels, tables, charts…),
   stored as ``view.json`` inside the project. Each component has *props* (its configuration), an optional
   *position* in its parent container, *bindings* that feed props from tags or expressions, and *events* that run
   actions or scripts.

Designer
   Ignition's desktop editor. People edit views in it; PDK never overwrites their edits (see :doc:`guide/gateway`).

Tags
   Named values on the gateway, usually connected to devices. Views read and write them through bindings and
   scripts.

PDK concepts
------------

Sandbox project
   The only project PDK writes to. You create an empty Perspective project for it and name it in the gateway's
   configuration with ``-Dpdk.sandbox=<project>``. Agent work lives in the ``PDK/`` folder of that project.

Target project (optional)
   A project that receives finished views through an explicit *promote*, for example a copy of the production
   project imported into the development gateway. Named with ``-Dpdk.target=<project>``.

Schema bundle
   The JSON schemas and component descriptors that Perspective itself ships, extracted from your own Ignition
   install into ``schemas/<version>/``. PDK validates against them, so it knows exactly the components and props of
   *your* version. They are never distributed with PDK.

Work folder
   Local copies of views (``work/<project>/<view path>/view.json``), each with a ``.pdk-base.json`` file that
   remembers the version it was read at.

Diagnostic
   A problem report with a stable code (for example ``P001``), a severity, a JSON pointer to the exact place in the
   ``view.json``, the component path, a message and a hint on how to fix it. See :doc:`reference/diagnostics`.

Eyes
   The part of PDK that opens a deployed view in a headless Chromium browser, measures it and combines what it sees
   with what the gateway reports.

Agent and MCP
   An *agent* is an AI model that can run tools, such as Claude Code. The *Model Context Protocol* (MCP) is the
   standard way to offer it tools; PDK ships an MCP server, ``pdk-mcp``, with one tool per command.

How the parts fit together
--------------------------

PDK has three layers: a Python package that does the work, a thin Java module that runs inside the gateway, and the
integrations that let an agent use them.

.. mermaid::

   flowchart LR
     agent["Agent<br/>(Claude Code or any MCP client)"]
     cli["pdk CLI"]
     mcp["pdk-mcp<br/>MCP server"]
     pkg["pdk Python package<br/>validator · catalog · sync · eyes · tags"]
     bundle[("Schema bundle<br/>schemas/&lt;version&gt;")]
     work[("Work folder<br/>work/&lt;project&gt;/…")]
     chromium["Headless Chromium"]
     module["pdk-gateway module<br/>(inside Ignition)"]
     gateway[("Ignition gateway<br/>sandbox project · tags")]

     agent --> mcp
     agent --> cli
     mcp --> pkg
     cli --> pkg
     pkg --> bundle
     pkg --> work
     pkg -- "loopback REST + token" --> module
     module --> gateway
     pkg --> chromium
     chromium -- "Perspective session" --> gateway

The verification loop
---------------------

A typical cycle, run by the agent or by you from the command line:

1. **Look up** the components and props in the catalog (``pdk catalog``), which comes from the schema bundle.
2. **Write** the ``view.json``. With Claude Code, a hook validates every save automatically.
3. **Validate** offline (``pdk validate``): structure, props, bindings, expressions, scripts and references.
4. **Deploy** to the sandbox (``pdk deploy``): the view is validated again and written with the signature of the
   version it was based on, so a concurrent Designer edit is detected instead of overwritten.
5. **Inspect** the running view (``pdk inspect``): layout problems, bad tag qualities, failing scripts and browser
   errors, each reported on its component. Expectations (``--expect``) check that the layout and behavior match the
   request; actions (``--act``) click, type and set tags first.
6. **Fix** and repeat until the view is clean, then **promote** it to its final place.

What PDK is not
---------------

- It is not a code generator or a model: the agent writes the views; PDK checks them.
- It is not a replacement for the Designer or for review by a person. A clean report means no detectable defects,
  not that the screen meets every requirement.
- It is not meant for production gateways. See :doc:`architecture` for the safety model.
