Working with the gateway
========================

This chapter covers how views move between your machine and the development gateway: pulling, deploying,
promoting and deleting, how conflicts with the Designer are handled, the project stylesheet and the optional target
project. All of it goes through the PDK module installed on the gateway (see :doc:`../architecture`).

Where things live
-----------------

On the gateway
   Agent work lives in the ``PDK/`` folder of the sandbox project, for example ``PDK/Pump``. Every deployed view
   also gets a preview page at ``/pdk/<view path>`` so it can be opened in a browser:
   ``http://127.0.0.1:8088/data/perspective/client/<sandbox>/pdk/PDK/Pump``.

On your machine
   ``work/<project>/<view path>/view.json``, next to a ``.pdk-base.json`` file that records the *signature* (a
   hash) and the text of the version it was based on. The ``work/`` folder is not versioned; set ``PDK_WORK_DIR`` to
   move it.

The commands
------------

``pdk pull <path>``
   Downloads a view (or every view under a folder) from the sandbox, or from another project with ``--project``,
   into the work folder. It refuses to overwrite local edits unless you pass ``--force``.

``pdk deploy <folder>``
   Validates the local view and writes it to the sandbox with the signature recorded at pull time. A new view (no
   ``.pdk-base.json``) is created; a pulled one is modified. Prints the preview URL.

``pdk promote <folder> [--to <path>] [--to-project target]``
   Copies a deployed ``PDK/X`` view to ``X`` (or to ``--to``) in the sandbox, or, with ``--to-project target``, to
   the target project.

``pdk delete <folder>``
   Deletes a pulled view on the gateway, only if nobody changed it since the pull. The local copy stays.

With Claude Code these are the ``pdk_pull``, ``pdk_deploy``, ``pdk_promote`` and ``pdk_delete`` tools.
``pdk_promote`` and ``pdk_delete`` are marked as destructive: the agent should run them only when asked.

Signatures and conflicts
------------------------

Every write sends the signature of the version it was based on. If the view changed on the gateway since then (a
Designer save, another agent), the module refuses the write with HTTP 409 and nothing is overwritten. PDK reports
it as ``G008``, with a diff by JSON pointer of what changed on the gateway and what you changed, and saves the
gateway's version next to yours as ``view.gateway.json``.

To resolve a conflict:

1. Read the diff and merge the gateway's changes from ``view.gateway.json`` into your ``view.json``.
2. Deploy again with ``--resolved`` (``resolved=true`` in the MCP tool). This writes over the gateway version you
   reviewed, and only that one: if it changed again in the meantime, you get a new 409.

The Designer shows changes made by PDK after *Project → Update Project* (Ctrl+Shift+U).

The project stylesheet
----------------------

The project's ``stylesheet.css`` (style classes written as ``.psc-<name>``) is handled like a view:

.. code-block:: bash

   uv run pdk pull stylesheet                                  # → work/<sandbox>/_stylesheet/stylesheet.css
   uv run pdk deploy work/<sandbox>/_stylesheet                # checked, signed, 409 on conflict

On a conflict the gateway version is saved as ``stylesheet.gateway.css`` and the report is a unified diff. The
Claude Code hook also checks the CSS on every save.

.. _target-project:

The target project
------------------

A target project receives finished screens, for example a copy of the production project imported into the
development gateway, so the result can be reviewed in its real context. It is configured on the gateway with
``-Dpdk.target=<project>`` and must differ from the sandbox. It is deliberately restricted:

- It only receives views **outside** ``PDK/`` and the stylesheet, and only through
  ``pdk promote … --to-project target``. There is no direct deploy, no preview page and no page configuration.
- A promote is refused if the view references views under ``PDK/`` (promote embedded views and popups first), and
  warns about references the target does not have.
- The stylesheet is edited in the sandbox and copied with
  ``pdk promote work/<sandbox>/_stylesheet --to-project target``.

Agents must promote to the target only with the explicit approval of a person.

Watching the gateway
--------------------

``pdk gateway`` gives a few read-only views of what happens on the gateway:

.. list-table::
   :header-rows: 1
   :widths: 25 75

   * - Command
     - Shows
   * - ``pdk gateway health``
     - Module, Ignition and Perspective versions, sandbox and target, and whether writes are enabled.
   * - ``pdk gateway changes``
     - Resource changes in the sandbox (who changed what, including the Designer), since a sequence number.
   * - ``pdk gateway scan``
     - Asks the gateway to rescan projects from disk, then lists the resulting changes.
   * - ``pdk gateway sessions``
     - Open Perspective sessions of a project and their pages.
   * - ``pdk gateway logs``
     - Recent gateway log events, filtered by project or view.
