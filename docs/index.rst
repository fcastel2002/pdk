PDK for Ignition Perspective
============================

**PDK** (Perspective DevKit) lets an AI agent build Ignition Perspective screens and prove they work before a
person opens them. It validates each view against the exact schemas of the Perspective version you have installed,
deploys it to a sandbox project on your development gateway, opens it in a real browser and reports what is wrong
on the component that has the problem.

.. code-block:: text

   catalog  →  write view.json  →  validate  →  deploy  →  inspect  →  fix  →  …

This documentation is organized from the general to the specific:

- :doc:`introduction` explains what PDK is, the Ignition concepts it relies on and how its parts fit together.
  Start here if you are new to Ignition or to agents.
- :doc:`installation` and :doc:`tutorial` take you from an empty machine to your first verified view.
- The :doc:`user guide <guide/index>` describes each task in depth: validating, looking up components, working with
  the gateway, inspecting views, simulating tags and using PDK from an agent.
- :doc:`configuration`, :doc:`architecture` and :doc:`troubleshooting` cover setup details, the design and the
  security model, and common problems.
- The :doc:`reference <reference/index>` lists every command, diagnostic code, MCP tool, REST endpoint and the
  Python API.

.. warning::

   PDK is for **development gateways only**. It installs an unsigned module that writes to a sandbox project. Never
   install it on a production gateway.

PDK is an independent open source project (Apache License 2.0). It is not affiliated with, endorsed by or
supported by Inductive Automation. Ignition and Perspective are trademarks of Inductive Automation.

.. toctree::
   :hidden:
   :caption: Getting started

   introduction
   installation
   tutorial

.. toctree::
   :hidden:
   :caption: User guide

   guide/index
   configuration
   architecture
   troubleshooting

.. toctree::
   :hidden:
   :caption: Reference

   reference/index

.. toctree::
   :hidden:
   :caption: Project

   GitHub repository <https://github.com/fcastel2002/pdk>
   Contributing <https://github.com/fcastel2002/pdk/blob/main/CONTRIBUTING.md>
   Changelog <https://github.com/fcastel2002/pdk/blob/main/CHANGELOG.md>
