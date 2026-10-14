Installation
============

Installing PDK has two parts: the **local install** (the ``pdk`` command, the schema bundle and a browser), which an
installer script does for you, and the **gateway setup** (a sandbox project, two configuration lines and the PDK
module), which needs administrator rights and is done by hand once.

Requirements
------------

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Requirement
     - Notes
   * - Ignition 8.3 with Perspective
     - Installed **on the same machine** as PDK: the PDK module only accepts loopback connections. Tested with
       Ignition 8.3.8–8.3.9 (Perspective 3.3.8–3.3.9). Any edition that can run a development gateway works,
       including Maker Edition and the trial mode.
   * - uv
     - The Python project manager from Astral. The installer installs it if it is missing, and uv brings its own
       Python 3.12 or newer: you do not need Python, git or a clone of the repository.
   * - JDK 17 or newer
     - Only to build the gateway module. PDK looks in ``JAVA_HOME``, the ``PATH`` and the usual install folders.
   * - Claude Code (optional)
     - Picks up PDK's MCP server, validation hook and skill automatically. Any other MCP client can use
       ``pdk-mcp``.

Local install
-------------

Run the installer for your operating system. It installs uv if needed, then PDK as a command
(``uv tool install pdk-perspective``), so ``pdk`` and ``pdk-mcp`` work from any folder. It sets up a folder for
Claude Code with ``pdk init`` (``./pdk-views``; set ``PDK_PROJECT`` to choose another), extracts the schema bundle of
your Perspective into ``~/.pdk/schemas/`` and downloads Playwright's Chromium. It then runs ``pdk doctor``, which
lists what is left to do on the gateway.

Windows (PowerShell):

.. code-block:: powershell

   irm https://raw.githubusercontent.com/fcastel2002/pdk/main/install.ps1 | iex

Linux and macOS:

.. code-block:: bash

   curl -LsSf https://raw.githubusercontent.com/fcastel2002/pdk/main/install.sh | sh

The installer never touches the gateway, ``ignition.conf`` or anything that needs administrator rights, and it is
safe to run again: it upgrades PDK and refreshes the folder.

.. tip::

   If you prefer to read a script before running it, download it first. The equivalent manual steps are:

   .. code-block:: bash

      uv tool install pdk-perspective
      pdk init my-views        # the folder you open with Claude Code
      pdk doctor --fix

On Linux, Chromium may need system libraries the first time:
``sudo "$(uv tool dir)/pdk-perspective/bin/playwright" install-deps chromium``.

Setting up a folder for Claude Code
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

``pdk init [folder]`` writes what Claude Code needs to work with PDK in that folder: the ``perspective-views`` skill
and the ``pdk-reviewer`` and ``pdk-log-reader`` subagents under ``.claude/``, the ``pdk`` MCP server in
``.mcp.json``, the hook that validates every ``view.json`` in ``.claude/settings.json``, and a PDK section in
``CLAUDE.md`` between ``<!-- pdk:begin -->`` and ``<!-- pdk:end -->`` markers. Existing files are merged: other MCP
servers, other hooks and the rest of ``CLAUDE.md`` stay. Views you pull go to ``work/`` in that folder. Any number of
folders can be set up; after an upgrade the MCP server refreshes the skill and subagents of the folder it starts in.

From a clone (to develop PDK)
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   git clone https://github.com/fcastel2002/pdk.git
   cd pdk
   uv sync
   uv run pdk doctor --fix

In a clone everything stays in the repository (``schemas/``, ``work/``, ``gateway/build/``) and the commands run as
``uv run pdk ...``; the clone is already set up for Claude Code. The rest of this page writes ``pdk ...``.

Gateway setup
-------------

``pdk doctor`` checks each of these steps and prints the exact fix for the first one that is missing. Run it
before you start and after each step.

Step 1: Create the sandbox project
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Create an empty Perspective project, for example ``pdk_sandbox``, from the gateway web page or from the
Designer. It is the only project PDK writes to. Do not use a real project.

Step 2: Edit ``ignition.conf``
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

As administrator, open ``<Ignition install>/data/ignition.conf`` and add two ``wrapper.java.additional.N`` lines,
using values of *N* that are not taken (``pdk doctor`` tells you which):

.. code-block:: ini

   wrapper.java.additional.10=-Dignition.allowunsignedmodules=true
   wrapper.java.additional.11=-Dpdk.sandbox=pdk_sandbox

The first line lets the gateway load unsigned modules; the second names the sandbox. Without
``-Dpdk.sandbox`` the module starts read-only. Restart the gateway.

Optionally, add a target project for finished views (see :ref:`target-project`):

.. code-block:: ini

   wrapper.java.additional.12=-Dpdk.target=<final_project>

Step 3: Create an API key
~~~~~~~~~~~~~~~~~~~~~~~~~

On the gateway web page, open *Platform → Security → API Keys* and create a key with security level
*Administrator* and *Require secure connections* unchecked. Save it in ``~/.pdk/ignition-api-key`` (or as
``api_key`` in the configuration file, see :doc:`configuration`). PDK uses it only to install the module and to
restart the gateway.

Step 4: Install the module
~~~~~~~~~~~~~~~~~~~~~~~~~~

.. code-block:: bash

   pdk gateway install --restart

This compiles the module's Java sources (shipped with PDK) against the jars of your own Ignition install, uploads the module through the
gateway's REST API, accepts its certificate (it is unsigned) and restarts the gateway.

Step 5: Verify
~~~~~~~~~~~~~~

.. code-block:: bash

   pdk doctor

Every line should read ``ok``:

.. code-block:: text

     ok  version        PDK 0.3.0 (latest release 0.3.0)
     ok  config         no ~/.pdk/config.toml: using environment variables and defaults (optional file)
     ok  python         Python 3.12.x, dependencies installed
     ok  ignition       C:\Program Files\Inductive Automation\Ignition (Ignition 8.3.8)
     ok  schemas        bundle for Perspective 3.3.8 in ~\.pdk\schemas\3.3.8
     ok  chromium       Playwright's Chromium at …
     ok  jdk            …\bin\javac.exe
     ok  ignition.conf  unsigned modules allowed, sandbox pdk_sandbox
     ok  gateway        http://127.0.0.1:8088 RUNNING
     ok  api key        accepted by the gateway (install and restart work)
     ok  module         dev.pdk.gateway 0.1.0, Perspective 3.3.8
     ok  sandbox        pdk_sandbox: writes enabled under PDK/
     ok  eyes           gateway tree and log tap available
   ready: every check passed

Optionally, from a clone, ``uv run pytest -m gateway`` runs the live tests. They write only under ``PDK/_selftest/`` in the
sandbox and delete what they create.

Updating
--------

PDK checks once a day whether a newer release is out (one request to GitHub's public API, cached in
``~/.pdk/update-check.json``) and says so on the next command, in ``pdk doctor``, and to the agent when the MCP
server starts, so it tells you at the start of the session. ``PDK_UPDATE_CHECK=0`` or ``update_check = false`` in
the configuration file turns that off.

To update, run the installer again, or:

.. code-block:: bash

   uv tool upgrade pdk-perspective

The MCP server refreshes the skill and subagents of a folder set up by ``pdk init`` the next time it starts there
(``pdk init`` does the same by hand). When the release notes mention the Java module, reinstall it with
``pdk gateway install --restart``: Ignition 8.3 does not reload modules without a restart. When you upgrade
Ignition, run ``pdk doctor --fix`` to extract the schemas of the new Perspective version. In a clone, update with
``git pull`` and ``uv sync``.

Uninstalling
------------

1. Remove the module from the gateway web page (*Platform → Modules*) and delete the two lines from
   ``ignition.conf``; restart the gateway.
2. Delete the sandbox project and the tags under ``[default]PDK/`` if you no longer need them.
3. ``uv tool uninstall pdk-perspective``, then delete ``~/.pdk/`` and the ``.claude/`` and ``.mcp.json`` that
   ``pdk init`` wrote in your folders (or the clone, if you used one).
