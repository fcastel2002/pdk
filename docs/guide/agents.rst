Using PDK from an agent
=======================

PDK is designed to be driven by an AI agent. Every command is also an MCP tool, the outputs are short and
structured so they fit in a model's context, and every diagnostic says where the problem is and how to fix it.
This chapter describes the Claude Code integration that ships with the repository, and how to connect any other MCP
client.

Claude Code
-----------

Open the PDK folder with Claude Code (run ``claude`` in it). The repository configures four things:

MCP server (``.mcp.json``)
   Starts ``pdk-mcp`` and offers its tools (see :doc:`../reference/mcp`). Claude Code asks you to approve the
   server the first time.

Validation hook (``.claude/settings.json``)
   After every file the agent writes or edits, ``pdk hook`` validates it if it is a ``view.json`` (or checks it if
   it is a ``stylesheet.css``) and sends the problems back to the agent, which fixes them before moving on.

Skill ``perspective-views`` (``.claude/skills/``)
   Instructions the agent loads when asked for a Perspective screen: the cycle to follow, the golden rules of
   ``view.json``, idioms and traps observed in real projects, and ten validated example views (*snippets*).

Subagents (``.claude/agents/``)
   Smaller models for closed tasks that would otherwise fill the main conversation: ``pdk-reviewer`` checks a
   deployed view against acceptance criteria using only inspections and screenshots; ``pdk-brief-runner`` builds a
   benchmark brief end to end; ``pdk-log-reader`` summarizes gateway logs; and ``pdk-mechanic``, ``pdk-auditor``,
   ``pdk-bundle-reader`` and ``pdk-sdk-reader`` help when developing PDK itself.

With that in place you can simply ask, for example: *"Build a pump status panel in PDK/Pump with the pump name as a
parameter, the setpoint from Sandbox/Setpoint and a running indicator; the header must span the full width."* The
agent looks up the components, writes the view, fixes what the hook reports, deploys it, inspects it with
expectations derived from your request and iterates until the report is clean. It then hands over the validation
and inspection summaries and the preview URL.

Rules the agent follows
-----------------------

The skill and the tool descriptions set a few rules that keep the agent within safe limits:

- Write only in the sandbox, under ``PDK/``; tags only under ``[default]PDK/``.
- Never overwrite a Designer edit: on a ``G008`` conflict, merge and redeploy with ``resolved``.
- Run destructive tools (``pdk_promote``, ``pdk_delete``) and promotions to the target project only when a person
  asks for them.
- Fix every error; fix or justify every warning.
- Check the request with expectations, not by looking at a screenshot.

.. _other-mcp-clients:

Other MCP clients
-----------------

Any client that supports MCP over standard input/output can use PDK. With PDK installed, the server is the
``pdk-mcp`` command. It keeps pulled views in ``work/`` of the folder it starts in: name the ``work/`` of the folder
you set up with ``pdk init`` in ``PDK_WORK_DIR`` (or start the server in that folder, where the client has a ``cwd``
setting):

.. code-block:: json

   {
     "mcpServers": {
       "pdk": {
         "command": "pdk-mcp",
         "env": {"PDK_WORK_DIR": "/path/to/screens/work"}
       }
     }
   }

The same entry in other clients' formats:

- Codex (``~/.codex/config.toml``, or ``.codex/config.toml`` in a trusted project): ``[mcp_servers.pdk]`` with
  ``command = "pdk-mcp"`` and ``env`` (or ``cwd``), plus ``startup_timeout_sec = 30`` and ``tool_timeout_sec = 180``:
  the eyes take longer than Codex's defaults.
- Gemini CLI (``.gemini/settings.json``): ``"mcpServers": {"pdk": {"command": "pdk-mcp", "env": {...}, "timeout":
  180000}}``.
- VS Code (``.vscode/mcp.json``): ``"servers": {"pdk": {"type": "stdio", "command": "pdk-mcp"}}`` (the key is
  ``servers``); Cursor (``.cursor/mcp.json``) uses ``mcpServers`` like the block above.

From a clone, run it through uv instead: ``"command": "uv", "args": ["--directory", "/path/to/pdk", "run",
"--quiet", "pdk-mcp"]``; then ``work/`` is the clone's unless ``PDK_WORK_DIR`` says otherwise.

The server describes its workflow in its instructions, so the model learns the cycle from the server itself. The
validation hook and the subagents are Claude Code's; elsewhere, have the agent call ``pdk_validate`` after writing a
``view.json`` (``pdk_deploy`` validates anyway). The skill's content (``.claude/skills/perspective-views/SKILL.md``
and ``reference.md``) is plain text and can be given to other agents as instructions, for example in ``AGENTS.md``.

Without an agent
----------------

Everything works from the command line as well (see the :doc:`../tutorial`), which is also the easiest way to
understand what an agent sees: the CLI and the MCP tools print the same reports.
