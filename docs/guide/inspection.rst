Inspecting running views
========================

Offline validation cannot tell whether a view *works*. The eyes can: ``pdk inspect`` opens a deployed view in a
headless Chromium browser, waits until it renders and its layout is stable, measures every component and combines
three sources about each one:

- the ``view.json`` that the gateway has (so every problem points into the file);
- the browser's DOM (boxes, text, colors, scrolling, errors in the console);
- the gateway's evaluated component tree (the quality of every binding) and its log (scripts that failed).

The report lists only anomalies, each on the JSON pointer of the component that has it, like the validator.

Commands
--------

.. code-block:: bash

   uv run pdk open PDK/Pump                     # render it and report load time and browser errors
   uv run pdk inspect PDK/Pump                  # anomalies
   uv run pdk inspect PDK/Pump --focus V001     # expand one code, component or pointer
   uv run pdk inspect PDK/Pump --viewport 1920x1080
   uv run pdk inspect PDK/Pump --viewport desktop,tablet,phone   # one report per viewport
   uv run pdk screenshot PDK/Pump --component root/Panel --out panel.png

The view is given by its path in the sandbox (``PDK/Pump``) or by its local folder. The default viewport is
1366×768; ``--viewport`` takes ``WIDTHxHEIGHT`` or a preset (``desktop`` 1366×768, ``tablet`` 768×1024, ``phone``
375×812, ``wide`` 1920×1080), and ``inspect`` takes several, comma-separated, with one report for each (the
token budget is shared). Inspection always looks at the **deployed** version: if your local copy differs, the report says so, and
``X002`` means the page does not render the ``view.json`` at all (deploy first).

Anomalies
---------

.. list-table::
   :header-rows: 1
   :widths: 10 25 65

   * - Code
     - Name
     - Meaning
   * - ``V001``
     - Text cut
     - A label, button or link shows less text than it needs (clipped or ended with an ellipsis). The message
       gives the width needed and the width shown.
   * - ``V002``
     - Component overflow
     - A component extends outside a parent that clips it.
   * - ``V003``
     - Viewport overflow
     - A component extends outside the browser window.
   * - ``V007``
     - Collapsed
     - A component with content was shrunk to zero size inside a parent that has room, typically a table or chart
       without ``basis`` or ``grow`` in a flex container.
   * - ``V010``
     - Not rendered
     - A component in the ``view.json`` has no element on the page.
   * - ``V011``
     - Embedded view failed
     - An embedded view or repeater did not load: check ``props.path`` and the parameters.
   * - ``V012``
     - Repeater instances overflow
     - A flex repeater's instances are bigger than the repeater along the axis their number does not explain (the
       cross axis without ``wrap``, the main axis with it), so each one scrolls inside a strip of the repeater.
   * - ``V013``
     - Low contrast
     - Text too close in color to what is behind it (WCAG AA: 4.5:1, or 3:1 for large text). The background is
       the first opaque one from the text outwards. Only text components whose colors the view chose are judged
       (not the theme's defaults, nor text drawn inside charts or tables); text over an image is not judged, and
       disabled controls are exempt.
   * - ``Q001``
     - Bad quality
     - A binding delivers a *Bad* quality, for example ``Bad_NotFound`` for a tag that does not exist.
       Points at the binding.
   * - ``Q002``
     - Error quality
     - A binding fails while evaluating: ``Error_ScriptEval``, ``Error_ExpressionEval``,
       ``Error_TypeConversion``. ``--focus`` shows the gateway's message or traceback.
   * - ``Q003``
     - Uncertain quality
     - A binding has not produced a good value yet.
   * - ``L001``
     - Script error
     - The gateway logged a script error for this component (an event handler or a startup script).
   * - ``C001``
     - Browser error
     - An error in the browser console.
   * - ``X001``
     - Eyes degraded
     - Part of the inspection was not available (for example the gateway's session API), so only the DOM was
       checked.
   * - ``X002``
     - Page out of sync
     - The page does not render this ``view.json``: deploy it first.

Expectations
------------

A clean inspection means nothing is broken, not that the view is what was requested. ``--expect`` turns the
request into checks; each one that fails comes back as ``A001`` with what was measured. Separate expectations with
``;`` or new lines. Components are written as their full name path from ``root``; quote names that contain
spaces.

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Expectation
     - Checks that…
   * - ``root/B below root/A [gap 8]``
     - B is below A; also ``above``, ``left-of``, ``right-of``. ``gap >= 8`` or ``gap 8..16`` for a range.
   * - ``root/Log/Last inside root/Log``
     - the component lies within the other's box (or its scrolled content).
   * - ``root/Save aligned right root/Form``
     - the edges line up: ``left``, ``right``, ``top``, ``bottom``, ``center-x``, ``center-y``.
   * - ``root/Table width 100%``
     - a size or position: ``width``, ``height``, ``x``, ``y``; operators ``=`` (default), ``>=``, ``<=``, ``>``,
       ``<``; pixels, or a percentage of the parent's content box (inside its border and padding, like CSS; for a
       coordinate container, its padding box). ``x`` and ``y`` are measured from the parent's inner top-left
       corner, like ``position.x`` and ``position.y`` in a coordinate container.
   * - ``root/Title text "Pump 1"``
     - the exact text; ``text ~ "Pump"`` for "contains". An embedded view or a repeater is read through the text
       its views render, so ``root/Summary text ~ "P-205"`` checks what reached it; a text field or text area,
       through its value.
   * - ``root/Alarm visible`` / ``hidden``
     - the component is rendered with a size (or not).
   * - ``root/Save enabled`` / ``disabled``
     - the control, or the input inside it, is enabled.
   * - ``root/Log scrolls [x|y]``
     - the content overflows and the component scrolls along that axis.
   * - ``root/Log/Last in-view``
     - the component is wholly in sight: not scrolled away, clipped or outside the window.
   * - ``root/Title color #323232``
     - text color; also ``bg`` (background), ``font-size [op] N`` and ``font-weight N``.
   * - ``root/Bar prop value >= 40``
     - a property as the gateway evaluated it: ``props`` by default (``prop value``), or ``position``, ``custom``
       or ``meta`` (``prop custom.total = 3``); nested paths like ``items[0].label``. Operators ``=`` (default),
       ``!=``, ``>=``, ``<=``, ``>``, ``<`` and ``~`` (contains). It reads values the DOM does not show, such as a
       progress bar's or a slider's ``value``.
   * - ``view prop custom.total = 3``
     - the same for the view's ``params`` or ``custom``.
   * - ``page /detail`` / ``page shows PDK/Detail``
     - the page's URL path inside the project (``page ~ det`` for "contains"; a full URL for an external one), or
       the view the page shows. Use them after a ``nav`` action.
   * - ``popup detail open`` / ``closed``
     - a popup with that id is open. Its components are written ``popup:detail/root/Name``.
   * - ``tag Sandbox/Setpoint = 42``
     - a simulated tag holds that value (read back from the gateway); ``tag X quality Bad_Stale`` for its quality.
   * - ``alarm North/Level active`` / ``cleared``
     - the alarms of the tags at or under that path are active (or cleared).

Lengths are compared with a 1-pixel tolerance. A malformed expectation is ``A002`` and one that names a component
that does not exist is ``A003``.

Actions
-------

``--act`` runs steps in the browser **before** measuring, so the expectations, the binding qualities and the
script errors all describe the state after them:

.. list-table::
   :header-rows: 1
   :widths: 45 55

   * - Step
     - Does
   * - ``click root/Save``
     - Clicks the component.
   * - ``type root/Name "P-2"``
     - Types into the input inside the component and confirms with Tab.
   * - ``row root/Table 2``
     - Selects the second visible row of a table.
   * - ``select root/Mode "Manual"``
     - Picks that option of a dropdown (several labels for a multi-select) and waits until the dropdown shows it.
   * - ``slide root/Level 7``
     - Moves a slider's handle with the keyboard until it reads 7 (A004 if 7 is not one of its steps).
   * - ``key Enter`` / ``key root/Name Enter``
     - Presses a key (Playwright names: ``Enter``, ``Escape``, ``Tab``, ``ArrowDown``, ``Control+A``) on the
       focused element, or on the input inside the component.
   * - ``hover root/Pump`` / ``double-click root/Pump``
     - Moves the mouse over the component, or double-clicks it.
   * - ``scroll root/List bottom``
     - Scrolls the component (or the first scrolling element inside it) ``down`` or ``up`` one screen, to the
       ``top`` or ``bottom``, or by N pixels.
   * - ``wait 500``
     - Waits that many milliseconds (at most 10,000).
   * - ``tag Sandbox/Setpoint 42 [quality Bad_Stale]``
     - Writes a simulated tag (see :doc:`tags`); the view receives it through its subscription.

After each step the page is allowed to settle. A step that cannot be done (a disabled button, a missing row) stops
the sequence as ``A004``; a malformed step is ``A005``. If a step opens a popup, the popup is inspected too and its
anomalies carry the ``popup:<id>/`` prefix.

If a step navigates (a ``nav`` action to a page or to a view), the report is about the view the page shows
afterwards, and a note says so: its anomalies, and the components named in ``--expect`` and ``--query``, are that
view's. ``page /detail`` and ``page shows PDK/Detail`` check where it went. A navigation to a page needs a route
for it in the project's page configuration; one to a ``url`` outside Perspective leaves only ``page`` to check.

Example: check that a button becomes enabled once a name is typed.

.. code-block:: bash

   uv run pdk inspect PDK/Form --act 'type root/Name "P-2"' --expect "root/Save enabled"

Measurements
------------

``--query`` prints what was measured for some components, which helps to write or tune expectations without a
screenshot. Terms are separated by commas: a name path (the component and its subtree), a component type
(``ia.display.label``), a pointer prefix or ``*`` for everything.

.. code-block:: text

   root/Title ia.display.label @12,12 1342x32 | "Pump P-101" | rgb(50, 50, 50) 20px 700 Noto Sans align start | flex column justify center

Each line gives the position and size in viewport pixels, the text, the color and font, and, when they are not at
their defaults, the background, padding, border, flex settings and scrolling.

Screenshots
-----------

``pdk screenshot`` saves a PNG of the view or of one component (``--component root/Panel``). Use it for what only
an image can judge, such as colors and visual hierarchy; measurements and expectations are cheaper and more precise
for everything else. ``--act`` runs the same steps as ``inspect --act`` before capturing, so a popup
(``--component popup:detail/root``) or the state after typing can be captured.

``--compare before.png`` checks what a change did to the picture: take a screenshot before editing, deploy the
change, and capture again with the same view, viewport and component. The report gives the share of pixels that
changed and the components where they are; the diff image (``<out>__diff.png``) shows the new capture faded with
the changed pixels in red. Changes outside the components you meant to touch are the regressions to look at.

.. code-block:: bash

   uv run pdk screenshot PDK/Pump --out before.png
   # edit, deploy
   uv run pdk screenshot PDK/Pump --compare before.png
   # compare: 0.4% of pixels differ in 1 region(s): root/Header/Title 93x13 @12,8

Sessions and the browser
------------------------

Each open view holds a Perspective session on the gateway, and some editions (Maker) limit how many can be open.
PDK reuses one browser and one session between calls; the MCP server closes its browser after two minutes without
use (``PDK_EYES_IDLE_S``). A browser that goes away only disconnects its session, which would keep its slot until it
times out, so when PDK closes its browser (at the end of a command, or when the MCP server's browser goes idle) it
also closes its sessions on the gateway. If a gateway still refuses a session, close other Perspective tabs or wait
for idle sessions to time out.
