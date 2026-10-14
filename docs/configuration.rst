Configuration
=============

PDK is configured through environment variables. To avoid setting them in every terminal, in the MCP server and in
the hook, they can be written in a TOML file, by default ``~/.pdk/config.toml`` (another file can be pointed to with
``PDK_CONFIG``). A variable that is already set in the environment takes precedence over the file.

Settings
--------

.. list-table::
   :header-rows: 1
   :widths: 22 28 50

   * - Key in ``config.toml``
     - Environment variable
     - Default and meaning
   * - ``gateway_url``
     - ``PDK_GATEWAY_URL``
     - ``http://127.0.0.1:8088``. The gateway's address; it must be this machine.
   * - ``ignition_home``
     - ``IGNITION_HOME``
     - The standard install path of each operating system (``C:\Program Files\Inductive Automation\Ignition``,
       ``/usr/local/bin/ignition`` or ``/usr/local/ignition``). Used to find the Perspective module, the jars and
       the module's token.
   * - ``api_key``
     - ``PDK_IGNITION_API_KEY``
     - Read from ``~/.pdk/ignition-api-key``. A gateway API key, used only to install the module and restart the
       gateway.
   * - ``work_dir``
     - ``PDK_WORK_DIR``
     - ``work/`` in the current folder (the one ``pdk init`` set up); in a clone, in the repository. Where pulled
       and new views live.
   * - ``schemas_dir``
     - ``PDK_SCHEMAS_DIR``
     - ``~/.pdk/schemas/``; in a clone, ``schemas/`` in the repository. Where schema bundles are extracted and read.
   * - ``perspective_version``
     - ``PDK_PERSPECTIVE_VERSION``
     - The newest bundle. Which bundle to validate against when several are extracted; the ``--perspective``
       option overrides it per command.
   * - ``update_check``
     - ``PDK_UPDATE_CHECK``
     - On. Once a day PDK asks GitHub's public API for the latest release and says when a newer one is out (on
       the next command, in ``pdk doctor`` and to the agent through the MCP server). ``false`` or ``0`` turns it
       off; it is always off when ``CI`` is set.

Example:

.. code-block:: toml

   gateway_url = "http://127.0.0.1:8088"
   ignition_home = "D:/Ignition"

``pdk doctor`` reports which settings the file applied, and flags unknown keys.

Other variables
---------------

``PDK_GATEWAY_TOKEN``
   The module's access token. Normally PDK reads it from ``<ignition_home>/data/pdk-gateway.token``, which the module
   rewrites on every start; set this variable only if that file is not readable by your user. On Linux and macOS
   the file is readable only by the gateway's user and group: join that group, or set this variable.
   ``pdk gateway restart`` still waits for the new token in the file, since this value does not change on restart.
``PDK_EYES_IDLE_S``
   Seconds without use after which the MCP server closes its browser (default 120), releasing its Perspective
   session.

Gateway settings
----------------

The sandbox and the target project are not configured in PDK but on the gateway, in ``ignition.conf``, so that a
client cannot change them:

``-Dpdk.sandbox=<project>``
   The only project PDK writes to. Without it the module is read-only.
``-Dpdk.target=<project>``
   Optional. The project that receives finished views through ``promote --to-project target``. It must differ from
   the sandbox; otherwise it is ignored.
``-Dignition.allowunsignedmodules=true``
   Required because the PDK module is not signed.

The client reads the sandbox and target names from the module (``uv run pdk gateway health``).
