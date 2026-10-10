Diagnostic codes
================

Every problem PDK reports has a stable code. A code never changes meaning between versions; new problems get new
codes. A diagnostic is printed as::

   <severity> <code> <JSON pointer> (<component path>) | <message> | hint: <hint>

where the severity is ``E`` (error), ``W`` (warning) or ``I`` (information). The same code can be reported with
different severities when PDK is more or less certain (for example ``B010`` is an error for ``and``/``or``/``not``
and a warning for an expression the offline parser does not understand). ``pdk catalog --topic codes`` prints this
list from the installed version.

Structure (S)
-------------

Produced by the validator for the shape of the ``view.json`` and its components.

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``S000``
     - invalid-json
     - The file is not valid JSON, or cannot be read.
   * - ``S001``
     - unknown-view-key
     - A top-level key that a view does not have (valid: ``root``, ``props``, ``custom``, ``params``,
       ``propConfig``, ``events``, ``permissions``).
   * - ``S002``
     - missing-root
     - The view has no ``root`` component.
   * - ``S003``
     - malformed-member
     - A view member that must be an object is not (for example ``"params": []``).
   * - ``S004``
     - unknown-component-key
     - A component key that does not exist (valid: ``type``, ``meta``, ``props``, ``position``, ``custom``,
       ``propConfig``, ``events``, ``scripts``, ``children``, ``version``).
   * - ``S005``
     - missing-type
     - A component without ``type``.
   * - ``S006``
     - unknown-component-type
     - A component type that this Perspective version does not have.
   * - ``S007``
     - children-on-leaf
     - ``children`` on a component that is not a container.
   * - ``S008``
     - missing-name
     - A component without ``meta.name``.
   * - ``S009``
     - root-name
     - The root component is not named ``root``.
   * - ``S010``
     - duplicate-name
     - Two siblings share a name, so paths to them are ambiguous.
   * - ``S011``
     - deprecated-component
     - The component type is deprecated in this version.

Props, meta and position (P)
----------------------------

Produced by the validator against the component schemas of the installed Perspective.

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``P001``
     - unknown-prop
     - A prop the component does not declare, usually a typo; the hint suggests the closest one.
   * - ``P002``
     - wrong-type
     - A prop value of the wrong type (for example a number where a string is expected).
   * - ``P003``
     - invalid-enum
     - A value outside the allowed set.
   * - ``P004``
     - missing-required
     - A required prop is missing.
   * - ``P005``
     - schema-violation
     - Any other violation of the prop's schema (range, pattern, item shape).
   * - ``P006``
     - unknown-style-key
     - A style key that is not a CSS property in camelCase (for example ``background-color``).
   * - ``P007``
     - unknown-meta-key
     - A key in ``meta`` that does not exist.
   * - ``P008``
     - invalid-name
     - A component name with characters that break paths. Names use letters, digits, spaces, ``_``, ``(``, ``)``
       and ``-``, and start with a letter or ``_``.
   * - ``P010``
     - unknown-position-key
     - A ``position`` key that the parent container does not use (for example ``x`` inside a flex container).

Bindings and ``propConfig`` (B)
-------------------------------

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``B001``
     - unknown-binding-target
     - A ``propConfig`` entry for a prop that does not exist.
   * - ``B002``
     - unknown-propconfig-key
     - A key in a ``propConfig`` entry that does not exist (valid: ``binding``, ``persistent``, ``access``,
       ``onChange``, ``paramDirection``).
   * - ``B003``
     - unknown-binding-type
     - A binding type that does not exist (the expression binding is ``expr``, not ``expression``).
   * - ``B004``
     - invalid-binding-config
     - Missing or unknown keys in a binding's ``config``.
   * - ``B005``
     - unknown-transform-type
     - A transform type that does not exist.
   * - ``B006``
     - invalid-transform
     - Missing or unknown keys in a transform.
   * - ``B007``
     - invalid-param-direction
     - A ``paramDirection`` other than ``input``, ``output`` or ``inout``.
   * - ``B008``
     - invalid-onchange
     - A malformed ``onChange`` (it needs a ``script`` string; unknown keys are warned).
   * - ``B009``
     - invalid-propconfig-value
     - A ``propConfig`` value outside what it accepts (``persistent`` that is not a boolean, an unknown
       ``access`` level).
   * - ``B010``
     - expression-syntax
     - An expression that does not parse: ``and``/``or``/``not`` instead of ``&&``/``||``/``!``, unbalanced
       parentheses or quotes (errors), or a construct the offline parser does not know (warning).

Events and scripts (E)
----------------------

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``E001``
     - unknown-event-group
     - An event group other than ``component``, ``dom`` or ``system``.
   * - ``E002``
     - unknown-event
     - An event the component does not fire.
   * - ``E003``
     - unknown-action-type
     - An action type that does not exist.
   * - ``E004``
     - wrong-action-scope
     - An action with the wrong scope: ``script`` runs on the gateway (``G``); ``nav``, ``popup``, ``dock`` and
       similar run in the browser (``C``).
   * - ``E005``
     - invalid-action-config
     - Missing or unknown keys in an action's ``config``.
   * - ``E006``
     - unknown-scripts-key
     - An unknown key in a component's ``scripts`` section.
   * - ``E007``
     - script-not-indented
     - A script body line without a leading tab. Ignition wraps every body in a function, so it would fail to
       parse at runtime.

References (R)
--------------

``R010`` to ``R015`` need a project (``--project``, or a view inside a project tree).

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``R001``
     - undeclared-view-param
     - A reference to ``view.params.x`` that the view does not declare.
   * - ``R002``
     - undeclared-custom
     - A reference to a ``custom`` property that is not declared.
   * - ``R003``
     - unknown-prop-ref
     - A reference to a section or property that its target does not have (a view has ``params``, ``custom``
       and ``props``).
   * - ``R004``
     - unresolved-component-path
     - A component path (``/root/Panel/Label`` or ``../Sibling``) that does not lead to a component.
   * - ``R005``
     - incomplete-ref
     - A reference that stops at a component without naming a property.
   * - ``R006``
     - unknown-session-prop
     - A ``session.props`` property that does not exist.
   * - ``R007``
     - unknown-ref-root
     - A reference whose first segment is not a known root (such as ``this``, ``parent``, ``view``,
       ``session``, ``page``) nor a component path (``/root/…``, ``../…``).
   * - ``R010``
     - view-not-found
     - An embedded view, repeater, popup or navigation target that the project does not have.
   * - ``R011``
     - param-undefined
     - A parameter passed to a view that does not declare it (Perspective would ignore it silently).
   * - ``R012``
     - param-direction-mismatch
     - A parameter passed to a view that declares it output-only, so passing it has no effect.
   * - ``R013``
     - required-param-missing
     - The target view declares a parameter without a default, and it is not passed.
   * - ``R014``
     - param-type-mismatch
     - A parameter value whose type differs from the type of the target's default.
   * - ``R015``
     - style-class-not-found
     - A style class that the project does not define.

Design lint (HMI)
-----------------

Information-level advice. ``HMI002``, ``HMI003``, ``HMI004``, ``HMI006``, ``HMI007`` and ``HMI008`` apply only with
``--profile hmi`` (the ``hmi-*`` design-token classes).

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``HMI001``
     - inconsistent-button-sizing
     - Buttons in the same group with different sizing (``basis``, ``grow``, ``shrink``).
   * - ``HMI002``
     - missing-command-variant
     - A button without a command variant class (primary, secondary, destructive).
   * - ``HMI003``
     - multiple-primary-actions
     - More than one primary action in a group.
   * - ``HMI004``
     - missing-group-spacing
     - A group of several children without a spacing class or gap.
   * - ``HMI005``
     - redundant-layout-wrapper
     - A flex container that only wraps one flex child with the same direction.
   * - ``HMI006``
     - missing-typography
     - A label without a typography class.
   * - ``HMI007``
     - mixed-control-heights
     - A button group that mixes control-height classes.
   * - ``HMI008``
     - missing-control-height
     - A button without a control-height class.
   * - ``HMI009``
     - empty-layout-group
     - A container without children.
   * - ``HMI010``
     - unsized-data-component
     - A table, chart or tree in a flex container without ``basis`` or ``grow``: it will collapse.
   * - ``HMI011``
     - alarm-onselect-not-row-selection
     - ``onSelect`` on an alarm table, which is text selection, not row selection.

Gateway (G)
-----------

Produced by the gateway client when a call to the PDK module fails.

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``G001``
     - gateway-unreachable
     - Nothing answers at the gateway URL.
   * - ``G002``
     - module-not-running
     - The gateway answers but the PDK module is not installed or not running.
   * - ``G003``
     - token-rejected
     - The token is missing or outdated (it changes on every gateway start).
   * - ``G004``
     - forbidden
     - The request was not loopback or carried proxy headers.
   * - ``G005``
     - write-disabled
     - The gateway runs without ``-Dpdk.sandbox``.
   * - ``G006``
     - outside-sandbox
     - A write outside the sandbox, or to the target project outside what it accepts.
   * - ``G007``
     - not-found
     - The project, resource, page, session or tag does not exist.
   * - ``G008``
     - conflict
     - The resource changed since it was read (signature mismatch). See :doc:`../guide/gateway`.
   * - ``G009``
     - bad-request
     - The request was malformed (path, signature, files).
   * - ``G010``
     - gateway-error
     - An unexpected error inside the module; see the gateway log.
   * - ``G011``
     - runtime-unavailable
     - Perspective's session API is not available on this version; the eyes fall back to the DOM.
   * - ``G012``
     - session-busy
     - The session or the tag provider is busy; retry.
   * - ``G013``
     - outside-tag-folder
     - A tag write outside ``[provider]PDK/``.

Eyes (V, Q, L, C, X)
--------------------

Produced by ``pdk inspect`` on a running view. See :doc:`../guide/inspection` for details.

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``V001``
     - text-clipped
     - Text cut off or ended with an ellipsis.
   * - ``V002``
     - component-overflow
     - A component extends outside a parent that clips it.
   * - ``V003``
     - viewport-overflow
     - A component extends outside the browser window.
   * - ``V007``
     - collapsed-component
     - A component with content shrunk to zero size.
   * - ``V010``
     - not-mounted
     - A component of the ``view.json`` that is not on the page.
   * - ``V011``
     - embedded-view-failed
     - An embedded view or repeater instance that did not load.
   * - ``Q001``
     - bad-quality
     - A binding with a *Bad* quality, such as ``Bad_NotFound``.
   * - ``Q002``
     - error-quality
     - A binding whose evaluation failed (``Error_ScriptEval``, ``Error_ExpressionEval``…).
   * - ``Q003``
     - uncertain-quality
     - A binding without a good value yet.
   * - ``L001``
     - script-error-logged
     - A script error the gateway logged for the component.
   * - ``C001``
     - browser-error
     - An error in the browser console.
   * - ``X001``
     - eyes-degraded
     - Part of the inspection was unavailable; the report is partial.
   * - ``X002``
     - page-out-of-sync
     - The page does not render this ``view.json``; deploy first.

Expectations and actions (A)
----------------------------

.. list-table::
   :header-rows: 1
   :widths: 10 30 60

   * - Code
     - Name
     - Meaning
   * - ``A001``
     - expectation-failed
     - An expectation is not met; the message includes the measured value.
   * - ``A002``
     - malformed-expectation
     - An expectation that cannot be read.
   * - ``A003``
     - expectation-unknown-component
     - An expectation that names a component the view does not have.
   * - ``A004``
     - action-failed
     - An action that could not be done (disabled control, missing row, tag write refused); the sequence stops.
   * - ``A005``
     - malformed-action
     - An action that cannot be read.
