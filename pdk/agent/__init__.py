"""PDK Agent sessions: what each tool call does (tools), and the rules `pdk-mcp --agent-session` enforces (gate).

An agent session is a runtime (the Claude CLI today) that a host such as the Designer's PDK Agent panel started with
PDK's MCP server and a session folder. The rules live here, in the server every runtime calls, so a runtime's own
settings cannot loosen them.
"""
