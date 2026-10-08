# Alternatives evaluation

No third-party backend capability has been validated during M0/M1. Before M2,
current documentation, source, APIs, and licenses must be checked for:

- FPP: remote media/sequence control, distributed synchronization, timecode
  input/output, and whether it can remain a replaceable backend.
- xLights: sequence formats and controller mapping that could be imported
  without coupling encounter semantics to an editor.
- QLC+: cue, media, and external-control behavior.
- LedFx: deterministic effect and WLED output patterns.
- mpv: supported IPC transports and timestamp semantics on the selected laptop
  operating system.
- WLED: current DDP packet format, realtime ownership, timeout, restoration,
  RGBW, and throughput behavior on the actual Dig-Uno.

This is an evaluation plan, not a claim that Cueverge must replace these
projects. License compatibility will be recorded with cited upstream versions
before code is reused.
