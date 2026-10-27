import type { McpScope } from "./api";

/** Optional MCP token scopes with plain-language labels, shared by show and installation tokens. */
export const optionalScopes: { value: McpScope; label: string }[] = [
  { value: "draft_edit", label: "Create isolated draft proposals" },
  { value: "direct_edit", label: "Edit working drafts directly and run offline bakes (no proposal approval)" },
  { value: "simulation", label: "Run no-hardware simulations and image previews" },
  { value: "publish_prepare", label: "Read publication readiness" },
  { value: "publish", label: "Run offline bakes, approve releases, publish, and stage packages" },
  { value: "activate", label: "Activate staged packages (live output)" },
  { value: "live_operation", label: "Operate the loaded show (physical output; operator/admin)" },
  { value: "input_config", label: "Configure installation camera connections (administrator; real events can trigger shows)" },
  { value: "installation_edit", label: "Edit installation photo descriptions (installation author; does not grant device control)" },
  { value: "photo_replace", label: "Replace reference photos using this project's image assets (keeps geometry; no device control)" },
  { value: "device_update", label: "Install controller agent updates on media hosts (administrator; restarts agents in maintenance)" },
];
