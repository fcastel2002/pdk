Installation
============

Installing PDK has two parts: the **local install** (the Python tools, the schema bundle and a browser), which an
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
   * - git
     - To clone the repository.
   * - uv
     - The Python project manager from Astral. The installer installs it if it is missing, and uv installs
       Python 3.12 or newer when needed.
   * - JDK 17 or newer
     - Only to build the gateway module. PDK looks in ``JAVA_HOME``, the ``PATH`` and the usual install folders.
   * - Claude Code (optional)
     - Picks up PDK's MCP server, validation hook and skill automatically. Any other MCP client can use
       ``pdk-mcp``.

Local install
-------------

Run the installer for your operating system. It clones PDK into ``./pdk`` (set ``PDK_DIR`` to choose another
folder), installs uv if needed, installs the Python dependencies, extracts the schema bundle of your Perspective and
downloads Playwright's Chromium. It then runs ``pdk doctor``, which lists what is left to do on the gateway.

Windows (PowerShell):

.. code-block:: powershell

   irm https://raw.githubusercontent.com/fcastel2002/pdk/main/install.ps1 | iex

Linux and macOS:

.. code-block:: bash

   curl -LsSf https://raw.githubusercontent.com/fcastel2002/pdk/main/install.sh | sh

The installer never touches the gateway, ``ignition.conf`` or anything that needs administrator rights, and it is
safe to run again: on an existing clone it pulls the latest version and repeats the checks.

.. tip::

   If you prefer to read a script before running it, download it first, or clone the repository and run
   ``./install.sh`` or ``.\install.ps1`` from the clone. The equivalent manual steps are:

   .. code-block:: bash

      git clone https://github.com/fcastel2002/pdk.git
      cd pdk
      uv sync
      uv run pdk doctor --fix

On Linux, Chromium may need system libraries the first time:
``sudo .venv/bin/playwright install-deps chromium``.

Gateway setup
-------------

``uv run pdk doctor`` checks each of these steps and prints the exact fix for the first one that is missing. Run it
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

   uv run pdk gateway install --restart

This compiles ``gateway/`` against the jars of your own Ignition install, uploads the module through the
gateway's REST API, accepts its certificate (it is unsigned) and restarts the gateway.

Step 5: Verify
~~~~~~~~~~~~~~

.. code-block:: bash

   uv run pdk doctor

Every line should read ``ok``:

.. code-block:: text

     ok  config         no ~/.pdk/config.toml: using environment variables and defaults (optional file)
     ok  python         Python 3.12.x, dependencies installed
     ok  ignition       C:\Program Files\Inductive Automation\Ignition (Ignition 8.3.8)
     ok  schemas        bundle for Perspective 3.3.8 in …\pdk\schemas\3.3.8
     ok  chromium       Playwright's Chromium at …
     ok  jdk            …\bin\javac.exe
     ok  ignition.conf  unsigned modules allowed, sandbox pdk_sandbox
     ok  gateway        http://127.0.0.1:8088 RUNNING
     ok  api key        accepted by the gateway (install and restart work)
     ok  module         dev.pdk.gateway 0.1.0, Perspective 3.3.8
     ok  sandbox        pdk_sandbox: writes enabled under PDK/
     ok  eyes           gateway tree and log tap available
   ready: every check passed

Optionally, ``uv run pytest -m gateway`` runs the live tests. They write only under ``PDK/_selftest/`` in the
sandbox and delete what they create.

Updating
--------

Run the installer again, or ``git pull`` followed by ``uv sync`` in the clone. When the Java module changed
(``gateway/``), reinstall it with ``uv run pdk gateway install --restart``: Ignition 8.3 does not reload modules
without a restart. When you upgrade Ignition, run ``uv run pdk doctor --fix`` to extract the schemas of the new
Perspective version.

Uninstalling
------------

1. Remove the module from the gateway web page (*Platform → Modules*) and delete the two lines from
   ``ignition.conf``; restart the gateway.
2. Delete the sandbox project and the tags under ``[default]PDK/`` if you no longer need them.
3. Delete the PDK folder and ``~/.pdk/``.
