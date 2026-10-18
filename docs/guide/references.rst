Project references and inventory
================================

Ignition has no cross-reference: renaming a UDT member or a tag folder breaks every screen that binds to it, and
the Designer cannot say which ones. ``pdk refs`` and ``pdk inventory`` read a project's files (a project folder, or
a project of the local gateway, read-only; the sandbox by default) and answer it.

Where a tag or a view is used
-----------------------------

.. code-block:: bash

   uv run pdk refs tag "Pump1/Running" --project PlantProject   # part of a path, a full path or a glob
   uv run pdk refs view Faceplates/Motor --project PlantProject

``refs tag`` lists every binding that uses a matching tag: direct, indirect, in expressions (``tag()``) and in
scripts (``system.tag.read*/write*`` with a full ``[provider]`` path), with the view, component, JSON pointer and
whether it writes. Indirect paths are expanded through the values the view's callers pass: a faceplate bound to
``{1}/Running`` with ``{1} = view.params.tagPath`` shows each pump it is embedded for, and which caller passes it.
The provider is optional and the match ignores case, like Ignition's tag paths.

``refs view`` lists who embeds, repeats, opens (popup), navigates or routes to a view, grouped by caller with the
params they pass, and what the view uses itself.

Inventory
---------

.. code-block:: bash

   uv run pdk inventory --project PlantProject                       # one row per view (Markdown)
   uv run pdk inventory --project PlantProject --format csv --out tags.csv   # one row per tag use and path
   uv run pdk inventory --project PlantProject --format json --out inventory.json

The inventory lists every view with its routes, params, callers, the views it uses, its tags (with the paths
indirect ones resolve to) and write points, its named queries and style classes, and the views nothing opens. The
CSV is a starting point for an IO list or a screen-by-screen tag report.

The same graph feeds validation: ``BP014`` (embedded views nested too deep) and ``BP015`` (heavy work in a view a
repeater shows once per instance), and ``--tags`` (tag paths checked on the gateway, expanded through the callers).

What it follows
---------------

- Embedded views (``ia.display.view``), flex repeaters (one set of params per instance), carousels, ``popup`` and
  ``nav`` actions, page routes and shared docks.
- A param passed as a literal value, or bound to a param of the caller (followed up to four levels).
- A reference to the component's own literal prop (``{this.props.params.tagPath}``).

Paths built from session properties, components' values or scripts are only known at runtime: they are listed as
written, and ``pdk inspect`` checks them in the running view (``Q001``).
