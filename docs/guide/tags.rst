Simulated tags
==============

Views are usually bound to tags connected to devices. To test those bindings without a device, PDK creates
**memory tags** under the ``PDK/`` folder of the gateway's tag provider (``[default]PDK/``) and lets you write their
values and qualities, read them back and check their alarms. They are ordinary Ignition tags, so a view binds to them
like to any other tag.

Paths
-----

A path without ``[provider]`` hangs from ``PDK/``: ``Sandbox/Setpoint`` means ``[default]PDK/Sandbox/Setpoint``.
Any tag can be **read**; only tags under ``PDK/`` can be **written**, created or deleted. A write outside it is
refused with ``G013``.

Creating tags
-------------

Tags are created from Ignition's own tag export format (the JSON the Designer exports), restricted to folders and
memory tags (``"valueSource": "memory"``), alarms included:

.. code-block:: json

   {
     "tags": [
       {"name": "Sandbox", "tagType": "Folder", "tags": [
         {"name": "Setpoint", "tagType": "AtomicTag", "valueSource": "memory", "dataType": "Float8", "value": 0}
       ]},
       {"name": "North Area", "tagType": "Folder", "tags": [
         {"name": "Tank 1 Level", "tagType": "AtomicTag", "valueSource": "memory", "dataType": "Float8",
          "value": 40, "alarms": [{"name": "High level", "mode": "AboveValue", "setpointA": 80,
                                   "priority": "High", "displayPath": "North Area/Tank 1"}]}
       ]}
     ]
   }

.. code-block:: console

   $ uv run pdk tags apply bench/tags/sandbox.json
   sandbox.json -> [default]PDK: 4 tags (Good 6)

Applying a file overwrites the tags it contains, which also resets their values and acknowledges their alarms: run
it before a test to start from a known state. ``--to`` imports into a subfolder of ``PDK/``. OPC tags, reference
tags, UDT instances, event scripts, history and alarm pipelines are refused, so a test file cannot reach a device or
send notifications.

Commands
--------

.. code-block:: bash

   uv run pdk tags list Sandbox --depth 2
   uv run pdk tags read Sandbox/Setpoint
   uv run pdk tags write Sandbox/Setpoint 42
   uv run pdk tags write Sandbox/Setpoint --quality Bad_Stale     # keep the value, change the quality
   uv run pdk tags alarms "North Area"
   uv run pdk tags delete Sandbox

Values are parsed as JSON numbers, ``true``/``false`` and ``null``; anything else is text.

In inspections
--------------

The usual way to use simulated tags is inside an inspection: an action sets the tag, the view reacts through its
subscription, and expectations check the result.

.. code-block:: bash

   uv run pdk inspect PDK/Pump --act "tag Sandbox/Setpoint 42" --expect 'root/Setpoint text "42"'
   uv run pdk inspect PDK/Form --act 'type root/Value "5"; click root/Send' --expect "tag Sandbox/Setpoint = 5"
   uv run pdk inspect PDK/Alarms --act 'tag "North Area/Tank 1 Level" 95' --expect 'alarm "North Area" active'

The first checks that a value reaches the screen, the second that a script writes what the user entered, the third
that an alarm fires and the alarm table can show it.

Tag limits
----------

Maker Edition caps the number of tags per gateway. Past the limit, new tags stay ``Bad_LicenseExceeded`` and refuse
writes until tags are deleted and the gateway restarts; PDK reports this with a hint when it happens.
