Looking up components
=====================

The catalog answers questions about the components, props, bindings and events of the Perspective version you
have installed. It reads the same schema bundle the validator uses, so what it lists is exactly what the validator
accepts. Agents are expected to consult it before writing a view; a prop that is not in the catalog does not exist.

Components
----------

Pass a component id, its short name or its display name:

.. code-block:: console

   $ uv run pdk catalog label
   ia.display.label (Label, display) -- A simple component for displaying text.
   props (4):
     alignVertical: enum[top|center|bottom] = "center" -- How to vertically align the text in ...
     style: style = {"classes": ""}
     text: string|number|boolean|null|object|array = "" -- Text to display
     textStyle: style = {"classes": ""}
   component events: none (DOM events go under events.dom, see topic=events)

For containers, the output also lists the ``position`` keys their *children* take (for example ``basis``, ``grow``
and ``shrink`` for a flex container, ``x``, ``y``, ``width`` and ``height`` for a coordinate container).

Nested props
------------

``--prop`` walks into objects and arrays; ``[]`` stands for "an item of the array":

.. code-block:: console

   $ uv run pdk catalog table --prop "columns[]"
   ia.display.table.columns[]: object
     keys (24):
       align: enum[top|center|bottom] = "center" -- Vertical content alignment.
       …
       justify: enum[left|center|right|auto] = "auto" -- Horizontal content alignment.
       …

Searching
---------

``--search`` finds components by id, name or description:

.. code-block:: console

   $ uv run pdk catalog --search trend
   1 match(es) for 'trend':
     ia.chart.xy (XY Chart, chart) -- A chart that displays data trends.

Reference cards
---------------

``--topic`` prints a compact card about a part of the ``view.json`` format:

.. list-table::
   :header-rows: 1
   :widths: 20 80

   * - Topic
     - Contents
   * - ``view``
     - The keys of a view, a component and ``propConfig``; how view parameters and positions work; a minimal view.
   * - ``bindings``
     - Binding types (``tag``, ``expr``, ``property``, ``expr-struct``, ``query``, ``tag-history``, ``http``), their
       configuration keys with the required ones marked, examples and the property paths they accept.
   * - ``transforms``
     - Transform types (``expression``, ``map``, ``format``, ``script``) and their keys.
   * - ``events``
     - Event groups (component and DOM events), action types and their scopes.
   * - ``style``
     - How ``style`` objects and style classes work.
   * - ``codes``
     - The list of diagnostic codes (see :doc:`../reference/diagnostics`).

Third-party components
----------------------

Components from modules other than Perspective are not in the extracted bundle. With the PDK module running,
``uv run pdk schemas sync`` reads the components the gateway actually registers, reports differences with the
bundle and adds the missing ones, so the catalog and the validator know them too.
