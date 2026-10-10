Command line
============

PDK's command line is ``pdk``, run through uv from the repository: ``uv run pdk <command> …``. Every command that
talks to the gateway accepts ``--url`` to override ``PDK_GATEWAY_URL`` for one call.

Exit codes: ``0`` success, ``1`` findings (errors in a validation or an inspection, a failed ``doctor`` check),
``2`` wrong usage.

``pdk screenshot`` writes its PNG to ``--out`` when given. Without it, the file goes to
``<work>/_screenshots/`` (``<work>`` is ``PDK_WORK_DIR`` or the repository's gitignored ``work/``), named after the
view path with ``/`` as ``__``, plus ``__<component>`` when ``--component`` is given: ``PDK/Nav`` with
``root/Panel`` gives ``PDK__Nav__root_Panel.png``. The command prints the path it wrote.

.. argparse::
   :module: pdk.cli
   :func: build_parser
   :prog: pdk
