# Functional change requirements

Every functional change or addition must keep the MCP interface and documentation
in sync with the implementation.

- Author-facing API and MCP capabilities must have an equivalent, discoverable UI
  workflow in the same change. Objects created through API/MCP must be visible,
  inspectable and editable in the UI, including objects without visual placement.
  Share validation, permissions and revision boundaries across all interfaces.
  Document any intentionally headless/operator-only exception explicitly.
- Update affected MCP tools, capability discovery, authoring schemas, descriptions,
  and simulation results so MCP clients can discover and use the behavior.
- Preserve MCP authorization and validation boundaries; add focused MCP regression tests.
- Update the relevant user/operator guides, configuration/schema documentation,
  and examples in the same change.
- Before declaring completion, verify the feature through its relevant MCP workflow
  and equivalent UI workflow as well as the underlying implementation. If no MCP
  or UI change applies, explain why.

See [MCP implementation](../src/cueverge/gui/mcp_server.py) and
[MCP operations](../docs/operations.md#mcp-assisted-authoring).
