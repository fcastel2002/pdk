Command line
============

PDK's command line is ``pdk``, run through uv from the repository: ``uv run pdk <command> …``. Every command that
talks to the gateway accepts ``--url`` to override ``PDK_GATEWAY_URL`` for one call.

Exit codes: ``0`` success, ``1`` findings (errors in a validation or an inspection, a failed ``doctor`` check),
``2`` wrong usage.

.. argparse::
   :module: pdk.cli
   :func: build_parser
   :prog: pdk
