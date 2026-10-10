Tutorial: your first verified view
==================================

This tutorial builds a small pump status panel from the command line and takes it through the whole cycle. It
assumes the :doc:`installation` is complete and ``uv run pdk doctor`` reports every check as ``ok``. The outputs
shown are real; on your machine the sandbox name and the numbers may differ. In the commands, replace
``pdk_sandbox`` with the name of your sandbox project (``uv run pdk gateway health`` prints it).

With Claude Code you would simply ask for the screen and the agent would run these same steps; doing them by hand
once shows what the agent sees.

1. Look up the components
-------------------------

The panel needs a flex container and some labels. Ask the catalog what a label accepts:

.. code-block:: console

   $ uv run pdk catalog label
   ia.display.label (Label, display) -- A simple component for displaying text.
   props (4):
     alignVertical: enum[top|center|bottom] = "center" -- How to vertically align the text in ...
     style: style = {"classes": ""}
     text: string|number|boolean|null|object|array = "" -- Text to display
     textStyle: style = {"classes": ""}
   component events: none (DOM events go under events.dom, see topic=events)

The catalog comes from the schemas of *your* Perspective version: if a prop is not listed, it does not exist.

2. Validate a first draft
-------------------------

Suppose a first draft contains the kind of mistakes models usually make. Save it anywhere and validate it:

.. code-block:: json

   {
     "params": {},
     "props": {"defaultSize": {"width": 400, "height": 200}},
     "root": {
       "type": "ia.container.flex",
       "meta": {"name": "root"},
       "props": {"direction": "column", "justifyContent": "center"},
       "children": [{
         "type": "ia.display.label",
         "meta": {"name": "Title"},
         "position": {"x": 10, "basis": "32px"},
         "props": {"txt": "Pump 1"},
         "propConfig": {"props.style.color": {"binding": {"type": "expression",
           "config": {"expression": "if({view.params.running} and 1, 'green', 'gray')"}}}}
       }]
     }
   }

.. code-block:: console

   $ uv run pdk validate broken.view.json
   broken.view.json (Perspective 3.3.8) | 6 errors
   E P010 /root/children/0/position/x (root/Title) | unknown position key 'x' for children of ia.container.flex | hint: valid: align, basis, display, grow, shrink
   E B010 /root/children/0/propConfig/props.style.color/binding/config/expression (root/Title) | expression syntax: 'and' is not an operator | hint: the expression language has no 'and': use && (&& and, || or, ! not)
   E R001 /root/children/0/propConfig/props.style.color/binding/config/expression (root/Title) | 'view.params.running': view param 'running' is not declared | hint: declare it: "params": {"running": null}
   E B003 /root/children/0/propConfig/props.style.color/binding/type (root/Title) | unknown binding type 'expression' | hint: use 'expr'
   E P001 /root/children/0/props/txt (root/Title) | unknown prop 'txt' for ia.display.label | hint: did you mean 'text'?
   E P001 /root/props/justifyContent (root) | unknown prop 'justifyContent' for ia.container.flex | hint: did you mean 'justify'?

Each line reads *severity, code, JSON pointer (component path) | message | hint*. Perspective itself would have
accepted this file and silently ignored most of these mistakes. ``--focus`` expands the detail of some
diagnostics, here the props each component does declare:

.. code-block:: console

   $ uv run pdk validate broken.view.json --focus P001
   broken.view.json (Perspective 3.3.8) | focus 'P001': 2 of 6
   E P001 /root/children/0/props/txt (root/Title)
     unknown prop 'txt' for ia.display.label
     hint: did you mean 'text'?
     detail: declared: alignVertical, style, text, textStyle
   …

3. Write the view in the sandbox
--------------------------------

New views go in the work folder, under ``PDK/`` of the sandbox. Create
``work/pdk_sandbox/PDK/_docs/Pump/view.json`` with a corrected panel: a title bound to a view parameter, a label
bound to a simulated tag, and a status label that is deliberately too narrow for its text.

.. code-block:: json

   {
     "custom": {},
     "params": {"name": "P-101"},
     "propConfig": {"params.name": {"paramDirection": "input", "persistent": true}},
     "props": {"defaultSize": {"width": 360, "height": 160}},
     "root": {
       "type": "ia.container.flex",
       "meta": {"name": "root"},
       "props": {"direction": "column", "style": {"padding": "12px", "gap": "8px"}},
       "children": [
         {"type": "ia.display.label", "meta": {"name": "Title"},
          "position": {"basis": "32px", "shrink": 0},
          "props": {"style": {"fontSize": "20px", "fontWeight": "bold"}},
          "propConfig": {"props.text": {"binding": {"type": "expr",
            "config": {"expression": "'Pump ' + {view.params.name}"}}}}},
         {"type": "ia.display.label", "meta": {"name": "Setpoint"},
          "position": {"basis": "24px", "shrink": 0},
          "propConfig": {"props.text": {"binding": {"type": "tag",
            "config": {"tagPath": "[default]PDK/Sandbox/Setpoint", "mode": "direct"}}}}},
         {"type": "ia.display.label", "meta": {"name": "Status"},
          "position": {"basis": "24px", "shrink": 0},
          "props": {"text": "This status text is far too long to fit in the space it was given",
                    "style": {"width": "120px", "whiteSpace": "nowrap", "overflow": "hidden"}}}
       ]
     }
   }

.. code-block:: console

   $ uv run pdk validate work/pdk_sandbox/PDK/_docs/Pump/view.json
   view.json (Perspective 3.3.8) | clean: no issues

The file is valid, but the status text will not fit. Offline validation cannot know that; the browser can.

4. Create the simulated tags
----------------------------

The setpoint label is bound to ``[default]PDK/Sandbox/Setpoint``. The repository ships a small set of memory tags
for this purpose:

.. code-block:: console

   $ uv run pdk tags apply bench/tags/sandbox.json
   sandbox.json -> [default]PDK: 4 tags (Good 6)

5. Deploy
---------

.. code-block:: console

   $ uv run pdk deploy work/pdk_sandbox/PDK/_docs/Pump
   created pdk_sandbox:PDK/_docs/Pump new -> 68bf196847
   preview: http://127.0.0.1:8088/data/perspective/client/pdk_sandbox/pdk/PDK/_docs/Pump

``deploy`` validated the view again, wrote it with a signature (``68bf196847``) and created a preview page. Open
the preview URL in a browser to see it, or open the sandbox project in the Designer.

6. Inspect
----------

.. code-block:: console

   $ uv run pdk inspect PDK/_docs/Pump
   pdk_sandbox:PDK/_docs/Pump @1366x768 (4 components) | 1 warning
   W V001 /root/children/2 (root/Status) | text cut: 'This status text is far too long to fit…' needs 448 px wide, shows 120 | hint: Give it room (position basis/grow, or a wider parent), let it wrap (style.whiteSpace normal), or shorten the text.

The eyes rendered the view at 1366×768 pixels, found that the status text needs 448 px and has 120, and reported it
on the exact component and pointer. Had the tag path been wrong, a ``Q001 Bad_NotFound`` would have pointed at the
binding; a failing script would show up as ``L001``.

7. Check the request, not only the defects
------------------------------------------

A clean inspection means nothing is broken, not that the view is what was asked for. Expectations turn the request
into checks, and actions set the stage first. Here the setpoint tag is set to 42, then the layout and the texts are
checked, and ``--query`` shows what was measured for the title:

.. code-block:: console

   $ uv run pdk inspect PDK/_docs/Pump --act "tag Sandbox/Setpoint 42" --expect "root/Setpoint below root/Title; root/Setpoint text \"42\"; root/Title text \"Pump P-101\"" --query root/Title
   pdk_sandbox:PDK/_docs/Pump @1366x768 (4 components) | 1 warning
   W V001 /root/children/2 (root/Status) | text cut: …
   act: 1/1 done
   expect: 3/3 passed
   measured 'root/Title' @1366x768: 1 component
   root/Title ia.display.label @12,12 1342x32 | "Pump P-101" | rgb(50, 50, 50) 20px 700 Noto Sans align start | flex column justify center

A failed expectation comes back as ``A001`` with the measured value, for example
``root/Status width >= 200: width is 120px, expected >= 200px``.

8. Fix, deploy, inspect again
-----------------------------

Remove the fixed ``width`` from the status label (or let it wrap with ``"whiteSpace": "normal"``), then deploy and
inspect again until the report is clean and every expectation passes.

9. Finish
---------

When the view is right, either **promote** it out of ``PDK/`` (``uv run pdk promote work/pdk_sandbox/PDK/_docs/Pump``
copies it to ``_docs/Pump``), or delete it:

.. code-block:: console

   $ uv run pdk delete work/pdk_sandbox/PDK/_docs/Pump
   deleted pdk_sandbox:PDK/_docs/Pump (68bf196847); removed the empty folder(s) PDK/_docs. …

Next steps
----------

- :doc:`guide/agents` shows how an agent runs this loop on its own.
- :doc:`guide/inspection` documents every anomaly, expectation and action.
- :doc:`guide/gateway` explains signatures, conflicts with the Designer and promotion to a target project.
