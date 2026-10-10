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

The validator runs six groups of checks, each with its own code family:

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

Project-aware checks
--------------------

References to other views, their parameters and style classes can only be checked against a project. Pass
``--project`` with either a project folder or the name of a project on the local gateway; inside a project tree
(including ``work/<project>/``) the project is detected automatically. The gateway's projects are only read.

What validation cannot see
--------------------------

Validation proves that a view is well formed for your Perspective version. It cannot tell whether the text fits,
whether a tag exists, whether a script fails when it runs or whether the screen looks as requested. That is the job
of :doc:`inspection`. When PDK cannot validate something offline (for example an expression construct its parser
does not know), it reports a warning rather than blocking.
