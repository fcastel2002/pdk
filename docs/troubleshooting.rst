Troubleshooting
===============

Start with ``uv run pdk doctor``. It checks the environment in order, from the local install to the gateway, skips
what depends on a failed check, and prints the fix for each problem. The sections below cover what it cannot fix by
itself.

Installation
------------

``no Ignition install found``
   PDK looks for Ignition in the standard path of your operating system. If it is elsewhere, set ``ignition_home``
   in ``~/.pdk/config.toml`` or ``IGNITION_HOME``.

``no schema bundle for the installed Perspective``
   Run ``uv run pdk doctor --fix`` (or ``uv run pdk schemas extract``). Repeat it after upgrading Ignition.

``Playwright's Chromium is not downloaded``
   Run ``uv run pdk doctor --fix`` or ``uv run playwright install chromium``. On Linux, also install the system
   libraries: ``sudo .venv/bin/playwright install-deps chromium``.

``no JDK 17+``
   Only needed to build the module. Install a JDK 17 or newer, or set ``JAVA_HOME``.

``uv sync`` fails on Windows with a file in use
   The MCP server keeps ``.venv/Scripts/pdk-mcp.exe`` open while a Claude Code session runs. Close the session,
   run ``uv sync``, and open it again.

Gateway and module
------------------

``G001`` gateway unreachable
   The Ignition service is not running, or ``gateway_url`` points elsewhere. The gateway must be on this machine.

``G002`` module not running
   Install it with ``uv run pdk gateway install --restart``. If it is installed, check *Platform → Modules* on the
   gateway web page; the gateway log explains why a module did not start.

``G003`` token rejected
   The token changes on every gateway restart. PDK reads it from ``<ignition>/data/pdk-gateway.token``; on Linux,
   make sure your user can read that file, or set ``PDK_GATEWAY_TOKEN``.

``G004`` forbidden
   The request did not arrive over loopback, or carried proxy forwarding headers. ``gateway_url`` must be a
   loopback address such as ``http://127.0.0.1:8088``; a host name or LAN address of this machine is refused.

``G005`` writes disabled
   The gateway runs without ``-Dpdk.sandbox``. Add it to ``ignition.conf`` and restart.

``G006`` outside the sandbox
   Writes go to the sandbox reported by ``pdk gateway health``; the target project accepts only views outside
   ``PDK/``, through ``promote --to-project target``.

``G008`` conflict
   Someone changed the view since you pulled it. See :ref:`the conflict procedure <conflicts>` below.

``G012`` session or tags busy
   A slow script or binding, or a large tag operation, keeps the gateway busy. Retry, and look for long-running
   work.

``G013`` outside the tag folder
   Simulated tags can be written only under ``[provider]PDK/``.

API key rejected during ``gateway install``
   Create the key with security level *Administrator* and *Require secure connections* unchecked, and save it in
   ``~/.pdk/ignition-api-key``.

.. _conflicts:

Resolving a conflict
--------------------

1. Read the diff in the ``G008`` report: it lists, by JSON pointer, what changed on the gateway and what you
   changed.
2. Merge the gateway's changes from ``view.gateway.json`` (saved next to your ``view.json``) into your file.
3. Deploy again with ``--resolved``.

If you want to discard your local changes instead, ``pdk pull --force`` replaces them with the gateway's version.

Inspection
----------

``X002`` page out of sync
   The page does not render the ``view.json`` you are inspecting: deploy it first.

Session refused
   Maker Edition limits concurrent Perspective sessions. Close other Perspective tabs or wait for idle sessions to
   time out; PDK reuses a single session between inspections.

``layout-unstable``
   Something keeps moving (an animation or a component that keeps growing). Inspect again, or check the component
   named in the message.

Bindings stay ``Q003`` (uncertain)
   The source has not delivered a value yet. Check the tag or query, and inspect again.

A tag binding reports ``Q001 Bad_NotFound``
   The tag does not exist at that path. For tests, create it with ``pdk tags apply`` and bind to
   ``[default]PDK/...``.

``Bad_LicenseExceeded`` tags
   The gateway is over its tag limit (Maker Edition). Delete tags you do not need and restart the gateway.

Getting help
------------

Open an issue on GitHub with the output of ``uv run pdk doctor``, the command you ran and its output, and, for a
view, the smallest ``view.json`` that shows the problem. Never attach views, tags or logs from a real project.
