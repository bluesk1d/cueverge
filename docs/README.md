# Cueverge documentation

## Choose your role

| Audience | Start here | Covers |
| --- | --- | --- |
| Technical operator / service administrator | [Service administration guide](service-administration.md) | Requirements, deployment, network/trust boundaries, media hosts, accounts, monitoring, upgrades, backup and recovery |
| Installation designer / show operator | [Installation and show operator guide](operator-guide.md) | Installation setup and mapping, projects/assets, timelines and autonomous behavior, rehearsal, publication, daily operation and incidents |

The two guides describe the current repository implementation. They separate
browser editing/simulation, rendered-artifact deployment, and the running
controller catalog. Important present limits include unprovisioned additional
installation records, simulation-only maintenance patterns, and the explicit
technical handover required for changed runtime scene logic.

## Supporting references

- [Persistent local UI sandbox](local-demo.md): generated demo assets and a
  hardware-free local controller for testing uploads, mapping, and UI changes.
- [Operations and simulation](operations.md): modes, diagnostics, MCP,
  deployment dependencies, publication/cache behavior, and recovery.
- [Show creator guide](show-creator-guide.md): creative capabilities,
  installation-specific examples, and a show brief template.
- [Scene schema](scene-schema.md), [events](events.md), and
  [device protocol v2](protocol.md): data and integration contracts.
- [Windows media agent](windows-media-agent.md) and
  [Linux media agent](linux-media-agent.md): host deployment and hardware checks.
- [GUI scale budgets](scale-budgets.md): qualification budgets and observability.
- [Architecture](architecture.md) and [architecture decisions](decisions/):
  system design and rationale.
- [GUI roadmap](gui-roadmap.md) and
  [show readiness roadmap](show-readiness-roadmap.md): plans and implementation
  history, not substitutes for operating procedures.

## Screenshot and verification scope

The guides contain seven integrated-browser screenshots in
[assets/gui](assets/gui/). Captures use the current production frontend and
an isolated loopback-only controller with the redistributable synthetic demo,
no DDP/MQTT adapters, and no enrolled devices. The courtyard reference image
was generated specifically for the walkthrough; no private venue imagery,
media, addresses, or credentials are shown.

The walkthrough verified sign-in, creation of an unprovisioned installation
record, project creation from the active catalog, reference-image upload,
mapper and timeline access, publication diagnostics, and the Operate surface.
The frontend passed its TypeScript/Vite production build.

The stock demo publication rehearsal stalled and made the isolated controller
unresponsive until restarted. That limitation is called out in the operator
guide; a countdown is not reported as a successful rehearsal.
Device enrollment against physical hosts, staging/activation/rollback,
production TLS/Compose deployment, backup restoration, and live output were
not exercised by this documentation capture. Qualify them on the target
installation before relying on them.
