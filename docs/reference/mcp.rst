MCP tools
=========

``pdk-mcp`` is an MCP server over standard input/output (see :doc:`../guide/agents` to register it). Each tool
mirrors a command of the :doc:`command line <cli>` and returns the same text report. Tools are annotated for the
client: *read-only*, *writes* (signed, non-destructive) or *destructive*. ``pdk_open`` and ``pdk_screenshot``
add the preview page to the sandbox's page-config (additive and idempotent); ``pdk_inspect`` is destructive because
its ``act`` steps run the view's own event scripts on the gateway, which can write tags or databases.

.. list-table::
   :header-rows: 1
   :widths: 18 12 70

   * - Tool
     - Kind
     - Arguments and behavior
   * - ``pdk_validate``
     - read-only
     - ``path`` (a ``view.json`` or a folder) or ``view`` (JSON text); ``project`` (folder or local gateway project,
       enables reference checks); ``focus`` (code, component path or pointer prefix); ``profile`` (``"hmi"`` for
       design-token lint). See :doc:`../guide/validation`.
   * - ``pdk_catalog``
     - read-only
     - ``type`` (component id or name), ``prop`` (nested path such as ``columns[].render``), ``search`` (text),
       ``topic`` (``bindings``, ``transforms``, ``events``, ``view``, ``style``, ``codes``). See
       :doc:`../guide/catalog`.
   * - ``pdk_pull``
     - writes locally
     - ``path`` (view or folder in the project, or ``stylesheet``), ``project`` (default: the sandbox), ``force``
       (overwrite local edits).
   * - ``pdk_deploy``
     - writes
     - ``path`` (local folder or ``view.json``, a sandbox path ``PDK/X``, or the ``_stylesheet`` folder);
       ``resolved`` (after a ``G008``, write over the reviewed gateway version). Validates first; returns the
       preview URL.
   * - ``pdk_promote``
     - destructive
     - ``path`` (local folder of a deployed ``PDK/`` view, or the ``_stylesheet`` folder), ``to`` (destination
       path, default without ``PDK/``), ``to_project`` (``"target"`` for the target project), ``resolved``.
   * - ``pdk_delete``
     - destructive
     - ``path`` (local folder of a pulled view), ``resolved``.
   * - ``pdk_tags``
     - destructive
     - ``action`` (``list``, ``read``, ``write``, ``apply``, ``delete``, ``alarms``, ``history``), ``path``,
       ``value`` (for ``history``, comma-separated values), ``quality``, ``file`` (tag export JSON for ``apply``),
       ``to`` (folder under ``PDK/``, or ``_types_/PDK`` for UDT definitions), ``depth``, ``every`` and
       ``provider`` (``history``). See :doc:`../guide/tags`.
   * - ``pdk_open``
     - writes (preview)
     - ``path`` (sandbox path or local folder), ``viewport`` (``WIDTHxHEIGHT``). Renders the view and reports load
       time, components rendered and browser errors.
   * - ``pdk_inspect``
     - destructive
     - ``path``, ``viewport``, ``focus``, ``query``, ``expect``, ``act``. See :doc:`../guide/inspection`.
   * - ``pdk_screenshot``
     - destructive
     - ``path``, ``viewport``, ``component`` (name path such as ``root/Panel``, or ``popup:<id>/root/...`` after
       ``act``), ``act`` (steps run before capturing, as in ``pdk_inspect``), ``compare`` (a PNG taken before:
       reports what changed and where, and adds a diff image). Returns a PNG image.
   * - ``pdk_pages``
     - writes
     - ``action`` (``list``, ``add`` or ``remove``), ``url`` (a route like ``/detail``), ``view`` (for ``add``, a
       view of the sandbox like ``PDK/Detail``), ``title`` (optional, for ``add``). Routes of the page configuration,
       so a ``nav`` action has a destination; ``remove`` only takes routes to views under ``PDK/``. See
       :doc:`../guide/gateway`.

The server's instructions describe the whole cycle (catalog, write, validate, deploy, inspect, fix), so a client
learns the workflow from the server itself. The browser used by ``pdk_open``, ``pdk_inspect`` and
``pdk_screenshot`` stays open between calls and closes after ``PDK_EYES_IDLE_S`` seconds without use, closing its
Perspective sessions on the gateway too.
