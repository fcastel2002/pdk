Gateway module REST API
=======================

The PDK module mounts its API at ``/data/pdk/v1`` on the gateway, for example
``http://127.0.0.1:8088/data/pdk/v1/health``. The Python client (:mod:`pdk.gateway.client`) is the supported way to
use it; this page documents it for reference and for other tools.

Access
------

Every request must:

- arrive over loopback (``127.0.0.0/8`` or ``::1``) and carry no proxy forwarding headers (``Forwarded``,
  ``X-Forwarded-For``, ``X-Real-IP``); otherwise the answer is ``403``;
- carry the header ``X-PDK-Token`` with the token the module writes to ``<ignition>/data/pdk-gateway.token`` on every
  start; otherwise the answer is ``401``.

Writes are further limited to the sandbox (``-Dpdk.sandbox``), the target project's finished views
(``-Dpdk.target``) and tags under ``[provider]PDK/`` (UDT definitions under ``[provider]_types_/PDK/``).

Endpoints
---------

.. list-table::
   :header-rows: 1
   :widths: 10 38 52

   * - Method
     - Path
     - Purpose
   * - GET
     - ``/health``
     - Module, Ignition, Perspective and Java versions, edition, sandbox and target (and whether they exist),
       whether writes are enabled, and the availability of the session API, the log tap and tags.
   * - GET
     - ``/projects/{project}/resources/{module}/{type}/{path…}``
     - A resource's files, signature and last modification; for a folder, its listing.
   * - PUT
     - ``/projects/{project}/resources/{module}/{type}/{path…}``
     - Create or modify a resource. Body: ``{"files": {name: content}, "signature": "<expected>"}``; no signature
       creates. An optional ``scope`` is the resource's application scope: a combination of ``G``, ``C`` and ``D``
       (each at most once), ``A`` (all) or ``N`` (none), default ``G``; anything else is a ``400``. Answers ``201``
       (created) or ``200`` (modified) with the new signature.
   * - DELETE
     - ``/projects/{project}/resources/{module}/{type}/{path…}?signature=…``
     - Delete a resource if its signature still matches.
   * - POST
     - ``/scan``
     - Ask the gateway to rescan projects from disk.
   * - GET
     - ``/changes?since=&limit=``
     - Resource change events in the sandbox (who, what, when) after a sequence number.
   * - GET
     - ``/catalog[?full=true]`` · ``/catalog/{id}``
     - The Perspective components registered on the running gateway.
   * - GET
     - ``/functions``
     - The expression functions (``name``, ``category``) and the ``system.*`` scripting names (from the gateway's
       scripting hints) of the running gateway.
   * - POST
     - ``/scripts/compile``
     - Compile Jython sources with the gateway's Jython, never running them. Body:
       ``{"sources": [{"id", "code"}]}`` (at most 500); answers ``{"results": [{"id", "ok", "line", "column",
       "message"}]}``.
   * - GET
     - ``/sessions?project=``
     - Open Perspective sessions of a project, with their pages and views.
   * - DELETE
     - ``/sessions?project=&page=``
     - Close the sandbox session that owns that page (PDK's own browser sessions, when it closes the browser).
   * - GET
     - ``/pages/{page}?project=``
     - The evaluated component tree of a page: props and binding qualities.
   * - GET
     - ``/logs?since=&limit=&project=&view=``
     - Gateway log events captured by the module, filtered by project or view.
   * - GET
     - ``/tags?path=&depth=``
     - Browse tags under a folder (any provider): type, value and quality.
   * - PUT
     - ``/tags?path=``
     - Import a tag export JSON under ``PDK/`` (folders, memory tags, instances of ``PDK/`` UDTs) or
       ``_types_/PDK/`` (UDT definitions with memory members).
   * - DELETE
     - ``/tags?path=``
     - Delete tags under ``PDK/``.
   * - POST
     - ``/tags/read``
     - Read the values and qualities of a list of tag paths.
   * - POST
     - ``/tags/write``
     - Write values, optionally with a quality, to tags under ``PDK/``.
   * - GET
     - ``/alarms?path=``
     - Alarm events of the tags under a path.
   * - POST
     - ``/alarms/ack?path=``
     - Acknowledge the alarms of the tags under a path.
   * - GET
     - ``/history``
     - The gateway's tag history providers.
   * - POST
     - ``/history``
     - Store synthetic history points for tags under ``PDK/``: ``{"provider", "points": [{"path", "value",
       "timestamp" (ms), "quality"?}]}`` (at most 10,000), through ``system.tag.storeTagHistory``.

Errors
------

Failures are JSON objects ``{"error": "<CODE>", "message": "…"}``, sometimes with extra fields (a ``409`` includes
the ``current`` version). The Python client maps them to the ``G`` codes in :doc:`diagnostics`.

.. list-table::
   :header-rows: 1
   :widths: 12 88

   * - Status
     - Codes
   * - 400
     - ``BAD_REQUEST``, ``BAD_SIGNATURE``, ``SIGNATURE_REQUIRED``, ``INVALID_PATH``, ``INVALID_PROJECT``,
       ``INVALID_FILE_NAME``, ``IS_FOLDER``, ``INVALID_TAGS``, ``INVALID_TAG_PATH``, ``UNKNOWN_QUALITY``
   * - 401
     - missing or wrong token
   * - 403
     - not loopback or proxied; ``WRITE_DISABLED``, ``OUTSIDE_SANDBOX``, ``OUTSIDE_TARGET``, ``OUTSIDE_TAG_FOLDER``
   * - 404
     - ``NOT_FOUND``, ``PROJECT_NOT_FOUND``, ``PAGE_NOT_FOUND``, ``TAG_NOT_FOUND``, ``TAG_PROVIDER_NOT_FOUND``
   * - 409
     - ``CONFLICT`` (signature mismatch), ``FOLDER_NOT_EMPTY``, ``IMMUTABLE``
   * - 413
     - ``TOO_MANY_TAGS``
   * - 422
     - ``PUSH_REJECTED``
   * - 500
     - ``RUNTIME_READ_FAILED``, ``TAG_OPERATION_FAILED``, ``TAG_DELETE_FAILED``, ``WRITE_NOT_VISIBLE``
   * - 501
     - ``RUNTIME_UNAVAILABLE`` (the session API did not link on this version)
   * - 503
     - ``BUSY``, ``INTERRUPTED``, ``PERSPECTIVE_UNAVAILABLE``
   * - 504
     - ``SESSION_BUSY``, ``TAGS_BUSY``
