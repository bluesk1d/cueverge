# Functional change requirements

Every functional change or addition must keep the MCP interface and documentation
in sync with the implementation.

- Update affected MCP tools, capability discovery, authoring schemas, descriptions,
  and simulation results so MCP clients can discover and use the behavior.
- Preserve MCP authorization and validation boundaries; add focused MCP regression tests.
- Update the relevant user/operator guides, configuration/schema documentation,
  and examples in the same change.
- Before declaring completion, verify the feature through its relevant MCP workflow
  as well as the underlying implementation. If no MCP change applies, explain why.

See [MCP implementation](../src/cueverge/gui/mcp_server.py) and
[MCP operations](../docs/operations.md#mcp-assisted-authoring).
