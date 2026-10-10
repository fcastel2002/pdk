Python API
==========

The ``pdk`` package can be used as a library, for example to validate views from another tool or a CI pipeline.
This reference is generated from the docstrings in the source code. The command line and the MCP server are the
supported interfaces; the Python API may change between minor versions until 1.0.

Example: validate a view from Python.

.. code-block:: python

   from pathlib import Path

   from pdk.bundle import load_bundle
   from pdk.diagnostics import render
   from pdk.validate import validate_file

   result = validate_file(Path("view.json"), load_bundle())
   print(render(result.diagnostics, title="view.json"))
   errors = [d for d in result.diagnostics if d["severity"] == "error"]

Validation
----------

.. automodule:: pdk.validate

.. automodule:: pdk.diagnostics

.. automodule:: pdk.bundle

.. automodule:: pdk.catalog

.. automodule:: pdk.project

Gateway
-------

.. automodule:: pdk.gateway.client

.. automodule:: pdk.gateway.module

.. automodule:: pdk.sync

.. automodule:: pdk.stylesheet

.. automodule:: pdk.tags

.. automodule:: pdk.live

Eyes
----

.. automodule:: pdk.eyes.inspect

.. automodule:: pdk.eyes.browser

.. automodule:: pdk.eyes.join

.. automodule:: pdk.eyes.rules

.. automodule:: pdk.eyes.measure

.. automodule:: pdk.eyes.expect

.. automodule:: pdk.eyes.act

Environment
-----------

.. automodule:: pdk.config

.. automodule:: pdk.doctor
