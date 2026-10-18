Validating views
================

``pdk validate`` checks ``view.json`` files offline, without a gateway, against the schema bundle of your
Perspective version. It is fast (milliseconds per view) and is the first check of every cycle: the Claude Code hook
runs it on every save, and ``pdk deploy`` runs it again before writing.

Running it
----------

.. code-block:: bash

   uv run pdk validate path/to/view.json          # one view
   uv run pdk validate path/to/folder             # every view.json below a folder
   uv run pdk validate view.json --project pdk_sandbox
   uv run pdk validate view.json --focus P001     # expand one code, component or pointer
   uv run pdk validate view.json --json           # machine-readable output

The exit code is ``0`` when there are no errors (warnings are allowed), ``1`` when there is at least one error and
``2`` on a usage problem, so it can be used in scripts and continuous integration.

Reading a report
----------------

.. code-block:: text

   broken.view.json (Perspective 3.3.8) | 6 errors
   E P001 /root/children/0/props/txt (root/Title) | unknown prop 'txt' for ia.display.label | hint: did you mean 'text'?

Each diagnostic has:

Severity
   ``E`` error (the view is wrong), ``W`` warning (probably wrong, or something PDK cannot check offline), ``I``
   information (design advice).
Code
   A stable identifier, such as ``P001``. Codes never change meaning; see :doc:`../reference/diagnostics`.
Pointer
   An RFC 6901 JSON pointer to the exact place in the file, such as ``/root/children/0/props/txt``.
Component path
   The component by name, from ``root``, such as ``root/Title``.
Message and hint
   What is wrong and how to fix it, often with a "did you mean" suggestion.

Reports are kept short on purpose (about 1,500 tokens, so an agent's context is not flooded). ``--focus`` with a
code, a component path or a pointer prefix expands the matching diagnostics with their ``detail`` line, and
``--budget`` changes the size limit.

What is checked
---------------

The validator runs seven groups of checks, each with its own code family:

.. list-table::
   :header-rows: 1
   :widths: 12 30 58

   * - Family
     - Area
     - Examples
   * - ``S``
     - Structure
     - Invalid JSON, missing ``root``, unknown component type, children on a component that is not a container,
       missing or duplicate names, deprecated components.
   * - ``P``
     - Props, meta, position
     - Unknown or misspelled props, wrong types, values outside an enumeration, unknown style keys, ``position``
       keys that belong to another kind of container.
   * - ``B``
     - Bindings and ``propConfig``
     - Binding to a prop that does not exist, unknown binding or transform types, malformed configuration,
       invalid parameter directions, expression syntax errors (``B010``).
   * - ``E``
     - Events and scripts
     - Unknown events or action types, actions with the wrong scope, scripts whose body is not indented (``E007``,
       which never runs at runtime).
   * - ``R``
     - References
     - Property paths to undeclared view parameters or custom properties, components that do not exist, and, with a
       project, embedded views that do not exist, undeclared or mistyped parameters, unknown style classes.
   * - ``HMI``
     - Design lint (information)
     - Data components without a size in a flex container (``HMI010``), alarm tables relying on ``onSelect``
       (``HMI011``), empty or redundant layout groups. With ``--profile hmi``, rules for the ``hmi-*`` design
       tokens.
   * - ``BP``
     - Bindings and performance
     - What Ignition integrators recommend: ``tag()`` in bindings, script transforms that should be map or
       expression transforms or that block, binding loops, polling and clocks, a tag bound many times, deep or
       heavy repeated embedded views, literal colors. See below.

Bindings and performance (BP)
-----------------------------

The ``BP`` rules encode what experienced Ignition integrators recommend for Perspective screens, from the forum's
regulars, Inductive Automation's ICC sessions on load time and the user manual. Only what breaks or costs is an
error or a warning; the rest is information, so a real project is not drowned in advice:

- **Errors and warnings:** binding loops (``BP005``); ``tag()`` in a binding (``BP001``: bind a custom property
  with an indirect tag binding instead); script transforms that read tags, query or call the network
  (``BP004``); change scripts that write their own property back without checking ``origin`` (``BP006``); input
  params bound inside the view (``BP007``); query bindings polling faster than every 10 s (``BP008``);
  ``now()`` faster than a second (``BP009``); ``runScript`` polling faster than 5 s (``BP010``); UDTs dropped by
  binding every member (``BP013``); embedded views more than three levels deep (``BP014``); queries in a view a
  repeater shows once per instance (``BP015``).
- **Information:** script transforms a map, expression or format transform would do (``BP002``, ``BP003``),
  expressions that only read one property (``BP011``), the same tag bound more than three times (``BP012``:
  bind it once to a custom property; subscriptions are shared, so this is about upkeep), literal colors and
  repeated inline styles (``BP016``, ``BP017``, one each per view), and bindings that move the layout (``BP018``).

``BP014`` and ``BP015`` follow embedded views and repeaters across the project, so they need ``--project`` (or a
view inside a project tree).

Tag paths on the gateway
------------------------

Offline, a tag path is only a string: a missing tag shows up when the view runs, as ``Q001 Bad_NotFound``. ``pdk
validate --tags`` (``tags=true`` in ``pdk_validate``) checks every tag path of the view on the gateway first, and
``pdk deploy`` does it on every deploy, as warnings that do not block:

- direct paths are checked as written;
- indirect, expression-mode and ``tag()`` paths are expanded through the values the view's callers pass for its
  params (embedded views, repeater instances, popups, navigation; see :doc:`references`), so ``{1}/Running`` with
  ``{1} = view.params.tagPath`` is checked for every pump the faceplate is embedded for, and ``R020`` names the
  caller whose value gives the missing tag;
- paths that depend on values known only at runtime (a session property, a component's value), or that only the
  param's default gives (often a placeholder), are counted once per view in ``R021``.

.. code-block:: bash

   uv run pdk validate work/<sandbox>/PDK/Pump --tags --project <sandbox>

Project-aware checks
--------------------

References to other views, their parameters, style classes and named queries can only be checked against a project. Pass
``--project`` with either a project folder or the name of a project on the local gateway; inside a project tree
(including ``work/<project>/``) the project is detected automatically. The gateway's projects are only read.

Functions and scripts checked against the gateway
-------------------------------------------------

The expression functions and the ``system.*`` scripting functions a gateway offers depend on its version and its
modules. ``pdk schemas functions`` reads them from the running gateway and saves them next to the bundle
(``schemas/<version>/functions.json``; ``pdk doctor --fix`` does it too). With that list, validation also reports
an expression that calls a function the gateway does not have (``B011``) and a script that uses a ``system.*`` name
it does not have (``E008``), both with "did you mean". Without it, those two checks are skipped.

A script's full syntax is checked by the gateway's own Jython 2.7: ``pdk deploy`` compiles every script of the view
before writing it (never running it) and refuses the deploy on ``E009``, with the line of the body. ``pdk validate
--compile`` (``compile=true`` in ``pdk_validate``) does the same without deploying.

.. code-block:: bash

   uv run pdk schemas functions
   uv run pdk validate work/<sandbox>/PDK/Pump --compile

What validation cannot see
--------------------------

Validation proves that a view is well formed for your Perspective version. It cannot tell whether the text fits,
whether a script fails when it runs or whether the screen looks as requested (``--tags`` covers whether its tags
exist). That is the job of :doc:`inspection`. When PDK cannot validate something offline (for example an expression construct its parser
does not know), it reports a warning rather than blocking.
