# Reactive Show Engine — Repository Bootstrap Specification

> **Status:** Initial product and architecture brief, 2026-10-08  
> **Audience:** GitHub Copilot working in a newly created, otherwise empty VS Code repository  
> **Purpose:** Supply enough context to design and implement a first working vertical slice without relying on prior chat history.  
> **Working name:** Reactive Show Engine (placeholder; do not treat as a finalized project or package name).

---

## 0. Instructions to the coding agent: read this first

You are bootstrapping a **new, potentially open-source software project** from an empty folder. Treat this document as the initial product brief and architectural intent, **not** as proof that any code, hardware integration, or test already exists. Do not claim requirements are implemented until they are verified.

**Your first assignment is to:**

1. Read this entire document and identify any material contradictions or decisions that would block a minimal implementation.
2. Propose a small, testable architecture and milestone plan consistent with the constraints below.
3. Create the repository scaffolding, documentation, automated tests, and a minimal working vertical slice; do not build a large UI or generalized plugin system prematurely.
4. Keep core logic independent of the user's specific house, IP addresses, holiday theme, and copyrighted media.
5. Preserve an explicit distinction among **decided**, **preferred**, **hypothesis requiring measurement**, and **future possibility**.
6. Ask focused questions only when an answer materially affects the next implementation step. Otherwise document assumptions and proceed incrementally.
7. Prefer maintainable, typed, documented, testable code over a rushed one-off demo.

**Do not:** replace WLED firmware, require a new LED controller, force a cloud service, make Home Assistant mandatory, assume precise wall-clock synchronization, claim that mpv's reported timestamp equals photons emitted by the projector, or implement facial recognition.

---

## 1. Executive summary

Build a **self-hosted, event-driven, stateful, distributed multimedia show-control system** for interactive physical installations. External events (camera detection, Home Assistant automation, MQTT, webhook, button press, schedules) can initiate or modify encounters and scenes. A central orchestrator coordinates remote media playback and real-time addressable lighting, with an extensible path to props, DMX, servos, fog machines, and additional displays.

An illustrative installation is an interactive exhibit: a media host drives a projector or display, speakers play the scene's audio, and a DDP-compatible RGB controller drives a light border. An optional Frigate camera detects visitors entering a configured interaction area and qualifies a welcome scene with synchronized lighting and sound. This fictional example demonstrates the architecture; it does not prescribe hardware, geometry, media, or camera labels.

**Core differentiator:** This is not primarily a holiday light sequencer or a fixed media playlist. It is a **reactive scene engine** with event ingestion, encounter state, priorities, composable effects, and timeline-based outputs. The media playback timeline can act as the authoritative clock for a synchronized scene, with the Docker host following actual reported playback progress rather than assuming a synchronized start time.

### Success in one sentence

A configured visitor event can select a welcome scene while LEDs and audio follow the **actual media playback position**, with predictable behavior under pause, seek, disconnection, restart, and repeated detections.

---

## 2. Product goals and non-goals

### Goals — first release trajectory

- Self-hosted and operable entirely on a local network; no internet dependency at runtime.
- Central controller suitable for container deployment on a Docker host.
- Lightweight agent on a separate laptop for video and audio playback.
- Real-time WLED pixel streaming from the Docker host, initially via DDP, with no firmware modification.
- Scene/event API suitable for Home Assistant; MQTT and webhooks are desirable integration paths.
- Declarative, reusable, versionable scene and show definitions.
- Stateful encounters with cooldowns, timeouts, trigger filtering, interruption and priority policies.
- Media playback timecode reported by the laptop and consumed by the Docker host to drive lighting.
- Deterministic, seekable lighting animations based on scene time and a physical pixel map.
- Unattended reliability, clean startup/shutdown, observable status, and safe recovery.
- Headless simulation and automated testing without the physical installation.
- Clean separation of reusable engine and installation-specific show package.
- Architecture that can be published as OSS after the initial implementation matures.

### Non-goals for the first vertical slice

- Replacing xLights, FPP, QLC+, or professional show-control suites feature-for-feature.
- A full drag-and-drop timeline editor, 3D house visualizer, or sophisticated browser UI.
- AI-generated real-time characters, live voice conversations, or tracking individual identities.
- Frame-perfect hardware genlock or SMPTE timecode equipment.
- Cloud orchestration, SaaS accounts, paid dependencies, or phone-app development.
- Supporting every lighting protocol and operating system immediately.
- Requiring a dedicated Raspberry Pi, new projector, new speaker, or new LED hardware.
- Making Home Assistant, Frigate, or Halloween-specific content a required core dependency.

---

## 3. Illustrative deployment and architectural constraints

This section describes example deployment roles. Choose actual hardware, geometry, audio routing, and network settings per installation; the product does not require a particular venue or device brand.

### 3.1 Controller host

- A **self-hosted Docker host** runs containerized services.
- Proposed central **show orchestrator** runs on a Docker host as a container with persistent configuration and logs.
- Docker host CPU, OS/container runtime, network interfaces, and resource budget are **not yet specified**. Avoid assumptions about hardware acceleration or platform-specific paths.
- Docker host is intended to own show state, trigger routing, scene selection, control commands, and (initially) LED frame rendering/streaming.

### 3.2 Media host

- A **media host** connects to a projector or display, for example over HDMI.
- Projector placement, throw distance, surface geometry, and viewing position are installation decisions.
- For multiple projection surfaces, content can use a composite canvas with geometry correction and black masking between surfaces. Specify the mapping and calibration workflow before producing final media.
- Laptop plays **video and audio from the same media file**, ideally using **mpv** under agent control; video goes to HDMI/projector, audio to laptop's analog output.
- Select an audio output on the media host. A wired speaker connection is one option; measure output latency rather than assuming zero delay.
- Laptop OS, GPU, exact display arrangement, projector model, and media-player version are TBD. Do not assume Windows-specific or Linux-specific mechanisms without first detecting/asking.
- Media files should be available locally to the laptop or cached ahead of playback; **do not rely on streaming media frames over the control channel**.

### 3.3 Lighting host and hardware

- Individually addressable RGB LEDs can outline a display, decorate a structure, or form a prop.
- A **DDP-compatible controller**, such as one running WLED, receives pixel frames without requiring custom firmware.
- Initial real-time output: **DDP over LAN/UDP** from the Docker host to WLED, subject to a hardware validation spike. WLED's current network settings, LED count, RGB/RGBW layout, segments, DDP behavior, and frame-rate ceiling must be measured rather than invented.
- Roof geometry, per-run start/end pixel indices, pixel directions, gaps, and physical measurements will be supplied later via a straight-on photo and mapping notes.
- Existing WLED presets should be recoverable after show control ends; do not permanently change the user's normal lighting configuration.
- Assume other automations may control WLED outside show sessions. Define clear resource ownership and restoration behavior.

### 3.4 Automation and perception

- Optional **Home Assistant** and **Frigate** integrations provide automation and camera observations.
- Frigate can produce person detections, zones, and occupancy-style events; use Home Assistant and/or MQTT as initial event bridges.
- A group of visitors should generally count as one encounter, not one event per person.
- No facial recognition, identity inference, or individual visitor profiling is needed.
- External triggers should be accepted through a generic event interface, not coupled to Frigate-specific schemas in core logic.

### 3.5 Network and operational assumptions

- The local network may have separate device VLANs; cross-VLAN connectivity, multicast behavior, firewall rules, and UDP delivery are **not guaranteed**.
- Prefer explicit configured hostnames/IPs and straightforward TCP/WebSocket control; DDP uses unicast UDP where possible.
- Secrets, credentials, and exact local addresses must be configuration, never hardcoded or committed.
- No assumption of sub-millisecond network latency or perfect packet delivery.

---

## 4. High-level architecture

```text
            +------------------------------+
            | Home Assistant / Frigate     |
            | schedules / webhooks / MQTT  |
            +---------------+--------------+
                            | external events / commands
                            v
            +------------------------------+
            | Docker host: Controller      |
            | - event ingestion            |
            | - encounter state machine    |
            | - scene selection/priority   |
            | - asset/agent readiness      |
            | - playback command routing   |
            | - authoritative show state   |
            | - timecode follower          |
            | - LED renderer & DDP output  |
            | - status / health / API      |
            +-------+----------------+-----+
                    |                ^
       commands     |                | media position/state
       over WS      v                | over WS
            +------------------------------+
            | Laptop: Media Agent          |
            | - local media asset cache    |
            | - mpv IPC control            |
            | - HDMI fullscreen video      |
            | - wired analog audio         |
            | - timecode/state telemetry   |
            +------------------------------+

            Docker host LED renderer --DDP/UDP--> RGB controller --> LEDs
```

### Ownership rules

- **Docker host owns** event policy, scene/encounter lifecycle, agent commands, resource arbitration, overall health, and lighting output.
- **Laptop owns** actual media playback and reports the position and playback state of that media.
- **For media-synchronized scenes, laptop playback position is authoritative for scene time.** The Docker host follows that clock; it must not blindly infer scene time from the command send time.
- **WLED owns** only the final physical pixel output; it should not independently run competing animations while under real-time show control.
- **Home Assistant owns** home-automation triggers and user controls, but should not be tasked with rendering media frames or precisely timing individual LED cues.
- Agent APIs and output adapters should remain replaceable. A later deployment might put the lighting renderer on the laptop or use FPP as a backend without changing scene semantics.

### Important architectural distinction

The Docker host is authoritative for **which scene is active**; the laptop's playback telemetry is authoritative for **where a media-backed scene is in time**. These are different forms of authority. Define behavior when telemetry is stale, the player pauses, or the laptop goes away.

---

## 5. Timecode-following synchronization design

### 5.1 Intended behavior

1. Controller selects scene and verifies media/lighting readiness.
2. Controller sends a `prepare` command to laptop agent; agent preloads the relevant media and confirms readiness.
3. Controller sends `play` and records a unique `playback_session_id` and scene instance ID.
4. Laptop agent observes actual player playback position and state through mpv IPC (or a better supported timing API if available).
5. Laptop publishes position telemetry at an initial target of approximately **10–20 Hz** over a persistent connection.
6. Docker host maintains a bounded estimate of current media time between telemetry samples.
7. Docker host renders deterministic lighting frames from estimated media time at an initial target of **20–30 FPS** and transmits them via DDP.
8. Calibration parameters account for fixed media/display/speaker/WLED latencies and observed transport delay.
9. On pause/seek/rate change/end/error/disconnect, lighting follows explicit scene policy.

### 5.2 Illustrative telemetry contract (not yet final)

```json
{
  "type": "media.position",
  "protocol_version": 1,
  "agent_id": "exhibit-display",
  "scene_instance_id": "scene-unique-id",
  "playback_session_id": "session-unique-id",
  "sequence": 42,
  "position_ms": 32450,
  "playback_rate": 1.0,
  "state": "playing",
  "duration_ms": 85000,
  "agent_monotonic_ms": 123456789
}
```

The `agent_monotonic_ms` field is diagnostic and can help estimate delay **only if a valid clock-offset estimate is maintained**; never subtract timestamps from unrelated monotonic clock origins directly. Docker host receipt time uses its own monotonic clock. Add round-trip measurement/offset estimation only if testing shows it is necessary. Reject stale session IDs, out-of-order sequences, and implausible position jumps.

### 5.3 Interpolation and correction

- At each accepted telemetry sample, anchor reported media position to Docker host receipt time, with optional bounded transport-delay correction.
- Between samples, estimate position using monotonic elapsed time × playback rate **only while playing**.
- Avoid unbounded extrapolation: after a configurable stale interval, freeze or fall back according to scene policy.
- On explicit seek, reset estimator immediately. On a small discrepancy, consider a bounded smoothing correction to avoid visual jitter; on a large discrepancy, snap to authoritative position.
- Handle mpv loop behavior and clip boundaries explicitly. Timecode can wrap on loops.
- A high-resolution **monotonic** clock is appropriate for measuring local intervals; synchronized absolute clocks are not a requirement for the MVP.
- An output frame is generated for a **target display time**, potentially offset to compensate for measured LED versus projector latency. Do not equate the moment a UDP packet is sent with the moment LEDs visibly update.

### 5.4 Calibration and realism

- mpv `time-pos` or equivalent is **not guaranteed to equal the frame currently visible on the projector**. HDMI/display buffering, video presentation scheduling, and projector processing introduce delay.
- An audio output path may include DSP buffering; wired analog removes Bluetooth/Cast session uncertainty, not all latency.
- WLED DDP reception and pixel refresh also add delay.
- Build a configurable `led_timing_offset_ms` (sign convention clearly documented) and optionally separate `audio_offset_ms` / `video_offset_ms` if the media pipeline permits.
- Provide a **flash-and-click calibration scene** and instructions to capture/measure with a phone slow-motion recording or other practical method.
- Initial alignment goals (not promises): ambient transitions ~100 ms; visible color changes ~50 ms; sharp audiovisual accents ideally ~30–50 ms if hardware allows. Measure before making performance claims.
- Network delay variability and the timestamp's sampling semantics are more important than nominal timestamp message frequency. Favor simple instrumentation before elaborate synchronization algorithms.

### 5.5 Behavior matrix

| Media condition | Initial LED behavior | Notes |
|---|---|---|
| Playing | Render at estimated position | Correct drift from telemetry |
| Paused | Hold deterministic frame or scene-defined pause look | No free-running animation unless explicitly configured |
| Seek | Recompute state at new position | Do not re-fire physical one-shot actions accidentally |
| Playback rate change | Follow new rate | Bound supported rates |
| Loop boundary | Reset to loop-relative position | Detect loop/session transitions |
| Completed | Run transition or restore ambient | Avoid stuck DDP takeover |
| Telemetry stale briefly | Bounded extrapolation | Configurable short grace period |
| Telemetry stale too long | Freeze, fade, or restore preset | Fail safely; do not run indefinitely |
| Agent disconnected | Mark degraded and stop media-dependent show | Continue HA availability and safe lighting |
| WLED unavailable | Mark degraded; media policy configurable | Do not pretend lights succeeded |
| Controller restart | Reconcile agent state; avoid duplicate scene firing | New controller epoch/session fencing |

---

## 6. Event-driven and interactive scene model

### 6.1 Domain concepts

**Event:** An external or internal observation: `person.entered_interaction_area`, `person.present_5s`, `interaction-area.motion`, `button.pressed`, `schedule.main_show`, `media.ended`, `agent.disconnected`.

**Condition/guard:** A predicate evaluated against current state, such as `show_enabled`, `encounter_active`, `person_still_present`, `cooldown_elapsed`, `nighttime`, `not_in_main_show`.

**Encounter:** A stateful interaction spanning multiple events and potentially multiple scenes. Has identity generated by the engine, not by identifying a person; tracks timestamps, current state, last trigger, and cooldown.

**Scene:** A coordinated, time-bounded or looping multimedia action with resource claims, media assets, lighting effects, cue tracks, completion behavior, and interruption policy.

**Show:** A named composition or playlist of scenes and transition rules; can be started by API/schedule/event.

**Cue:** A timeline-relative instruction or effect change, e.g., start roof ripple at 12.4 s, dim background, play a local sound, mark a state transition.

**Output/resource:** A logical destination such as `projector.exhibit`, `audio.main`, `roofline`, `fog.machine`, or `exhibit.props`, bound to physical devices in deployment config.

**Rule:** Event + guards + action/scene selection, with priority, debouncing, cooldown, and concurrency behavior.

### 6.2 Initial encounter state machine

```text
AMBIENT / UNAWARE
  person enters configured zone
       -> WELCOME (a character notices the visitor)
  person remains for N seconds
       -> ENGAGED (a longer scene becomes eligible)
  person remains in the interaction zone
       -> REACTION (display accent / light-border pulse)
  person leaves or sequence ends
       -> RESET / COOLDOWN
  cooldown expires
       -> AMBIENT
```

This is **illustrative show content**, not hardcoded engine behavior. A user-authored ruleset should be able to express different state machines and multiple possible reactions. Presence detection must be debounced; duplicate MQTT events must not create a flood of simultaneous scenes. For a group of visitors, one encounter is normally sufficient.

### 6.3 Priorities and resource arbitration

Suggested initial conceptual layers:

1. Safety/shutdown (highest priority).
2. Scheduled main show or explicit operator override.
3. High-impact interactive event.
4. Low-impact interactive encounter.
5. Ambient loop (lowest priority).

Define per-resource policies such as `exclusive`, `replace`, `queue`, `ignore`, or `duck`/`overlay` when supported. **Do not claim true simultaneous video layering until the media backend implements compositing.** For MVP, use authored transition clips and explicit media replacement. Lighting can later support composited layers if a deterministic blending model is implemented.

### 6.4 Repeated detections and selection variety

- Configurable per-event debounce and per-scene cooldown.
- Encounter-scoped idempotency keys to avoid double triggering.
- Event TTL so a delayed presence notification does not fire after a visitor leaves.
- Optional weighted selection among multiple eligible reactions, with seeded randomness for reproducibility in tests.
- Quiet-hours and show-enabled guards supplied through configuration/HA.
- Preserve explicit operator commands over low-priority automatic triggers.

### 6.5 Distinguish seekable rendering from irreversible actions

A pure lighting function `render(scene, timestamp, geometry)` may be evaluated repeatedly or out of order. An action such as fog, a motor, or a relay **must not** re-fire just because timecode moved backward. Design one-shot cue delivery with execution IDs, acknowledgment, deduplication, and safety limits separately from deterministic pixel rendering.

---

## 7. Scene definitions and installation packages

Use a human-readable declarative format such as YAML or JSON, validated against versioned schemas. YAML is a preference, not a mandate. Keep device roles logical and bind to physical hardware in a separate installation config.

### Illustrative pseudo-YAML (design sketch, not a committed schema)

```yaml
schema_version: 1
id: display-highlight
name: Display Highlight
mode: media_synchronized
priority: 60
resources:
  - projector.exhibit
  - audio.main
  - roofline
media:
  asset: media/display-highlight.mp4
  agent: exhibit-display
  display: projector
  audio_output: analog
lighting:
  output: roofline
  default_effect: dim-purple
  cues:
    - at_ms: 8200
      effect: charge-up
      parameters: { origin: border-left, duration_ms: 1900 }
    - at_ms: 10100
      effect: traveling-pulse
      parameters: { path: border-traversal, color: "#00FF66", duration_ms: 600 }
completion:
  transition_to: ambient-display
interruption:
  policy: queue_or_replace_by_priority
```

**Important:** Real scene validation should reject undefined paths, unbound resources, nonexistent media, invalid colors, and unsupported effect parameters before starting playback.

### Installation configuration

Separate files for:

- Controller settings (API/listen addresses, state store, log level).
- Agent registrations and capability declarations.
- Device bindings (`roofline` → WLED host; `projector.exhibit` → laptop media agent).
- Roofline pixel geometry (segments, direction, named paths, physical adjacency, pixel count, RGB/RGBW channels).
- Timing calibration offsets.
- Home Assistant/MQTT integration settings.
- Show content and local media assets.

The open-source repository should ship **synthetic demo geometry and freely redistributable demo assets**. Do not distribute unlicensed footage, logos, sound effects, or characters as bundled OSS samples.

---

## 8. Roofline mapping and animation

An illustrative light border can include horizontal and vertical sections, corners, and wiring gaps. **Physical geometry and wiring order are not interchangeable.** Build a mapping abstraction:

- Physical pixel index and controller/channel.
- Logical segments (`border-left`, `border-top`, `border-right`, etc.).
- Segment direction and ordered pixel lists.
- Named paths that can cross multiple segments.
- Optional approximate 2D coordinates (normalized or measured) for geometry-aware effects.
- Explicit gaps/discontinuities and branching paths.
- Channel/color-order handling, RGB versus RGBW, brightness and power limits.

The user will later provide a front-facing image annotated with start/end indices, directions, and dimensions. Do not fabricate a house map in production configuration. A synthetic test map is fine.

Initial effect primitives might include solid color, fade, pulse, chase, traveling pulse, sparkle, charge-up, flicker, blackout, and path-based sweep. Every effect should be a deterministic function of time, configuration, and pixel geometry. Compose effects in a controlled order, with explicit brightness caps and optional gamma/color correction.

Provide a headless renderer test that can assert exact RGB output for selected pixels/timestamps. A simple HTML/SVG or terminal-based simulator can come later; a full graphical editor is not needed for the first spike.

---

## 9. External interfaces and communication

### 9.1 Controller HTTP API (illustrative)

```text
GET  /health/live
GET  /health/ready
GET  /api/v1/status
GET  /api/v1/scenes
POST /api/v1/scenes/{scene_id}/start
POST /api/v1/shows/{show_id}/start
POST /api/v1/stop
POST /api/v1/events
GET  /api/v1/agents
```

Use explicit request/response schemas, meaningful HTTP errors, and idempotency support. Separate API commands from external observations: `start scene` is not the same as `person entered zone`.

### 9.2 Agent connection

- Persistent **WebSocket** is a reasonable initial choice for Docker host ↔ laptop command/telemetry.
- Define protocol version, agent identity, capabilities, connection/heartbeat, request IDs, acknowledgment, errors, and session fencing.
- Commands: `prepare`, `play`, `pause`, `resume`, `seek`, `stop`, `get_status`.
- Telemetry: player state, playback position, rate, duration, errors, device status, and optional latency diagnostics.
- Controller restart and agent reconnection must reconcile actual state; do not blindly replay stale commands.
- If laptop is behind a firewall, prefer agent-initiated outbound WebSocket connection to Docker host.

### 9.3 Home Assistant and Frigate

- Start with REST commands from HA automations/scripts and/or MQTT event ingestion.
- Publish state/status to MQTT or expose a polling endpoint; consider Home Assistant MQTT discovery later.
- A Frigate adapter should normalize camera/zone messages into generic events, with optional filtering in HA.
- Document sample HA automations and MQTT payloads, but do not make HA a runtime dependency.
- Consider an explicit `armed` switch, manual override, and dashboard status.

### 9.4 Security

- Bind APIs to LAN only by default; support token authentication for control endpoints and agents.
- Store secrets in environment variables or secret files; redact them in logs.
- Validate all event inputs and scene parameters. Avoid arbitrary shell execution or untrusted script evaluation in scene definitions.
- Restrict asset paths to configured directories; prevent path traversal.
- Apply sensible event rate limits and payload size limits.
- A local LAN is not a reason to omit authentication or authorization entirely.

---

## 10. Runtime behavior and reliability

- Controller runs continuously as a service/container; laptop agent can start automatically when the laptop boots/logs in.
- The agent should discover/validate projector display and audio output before reporting ready.
- Preload media to minimize black flashes or startup jitter; support configurable transitions.
- Restore a known WLED preset or relinquish real-time control on shutdown, scene completion, or extended agent failure. Test what actually happens when DDP frames stop; do not assume WLED automatically restores the desired state.
- Ensure only one active owner sends DDP frames to a given WLED device. Competing WLED presets/HA automations need a documented arbitration strategy.
- On failure, do not leave a fog machine or other future actuator energized.
- Make shutdown/recovery idempotent.
- Keep structured logs with scene instance ID, playback session ID, event ID, and agent ID.
- Provide metrics: scene start latency, timecode age, position correction magnitude, DDP FPS, send errors, connected agents, dropped/duplicate events, and resource contention.
- Offer a dry-run mode where events and scenes execute logically without sending hardware commands.
- Persist configuration and optionally minimal operational state; decide deliberately what survives controller restarts.
- Support safe configuration reload or restart; never silently change active hardware mapping mid-show.

---

## 11. Technology preferences and open choices

### Preferred initial stack

- **Python 3.12+** for controller and laptop agent, unless feasibility testing suggests a compelling alternative.
- **FastAPI** for HTTP API and WebSocket support.
- **Pydantic** or equivalent for configuration/protocol validation.
- **mpv** for media playback, controlled through its supported IPC interface.
- **DDP over UDP** for WLED output.
- **Docker/Compose** for Docker host deployment.
- **pytest**, type checking, linting/formatting, and GitHub Actions CI.
- Use the standard library for simple scheduling and state logic unless a dependency clearly improves correctness.

These are preferences, not immutable constraints. Choose versions after checking compatibility with the actual target machines.

### Questions intentionally unresolved

1. Laptop OS, exact audio/video device enumeration, and mpv IPC transport.
2. Whether the Docker host should render DDP long-term or whether a dedicated renderer agent becomes preferable.
3. Real WLED pixel count, channel layout, DDP capabilities, throughput, and brightness/power ceiling.
4. Actual network topology and connectivity between Docker host, laptop, HA, and WLED.
5. Precise calibration offsets for display, audio output, and lighting controller.
6. How to preload/switch clips seamlessly in the selected mpv architecture.
7. Whether scenes eventually need genuine simultaneous video layers, or authored clips/transitions are sufficient.
8. Persistence model: in-memory first, SQLite if durable event/state history becomes necessary.
9. How much show authoring belongs in code/YAML versus a future UI.
10. Final OSS name, license, public release timeline, and external contributor expectations.

**Do not silently turn unresolved questions into claims.**

---

## 12. Existing projects to evaluate before reinventing subsystems

- **xLights** — established holiday-light sequencing and controller mapping.
- **Falcon Player (FPP)** — playback, playlists, distributed synchronization, remote control; likely the most important architectural comparison.
- **QLC+** — theatrical lighting cues and media integration.
- **LedFx** — real-time lighting effects, potentially useful as a reference.
- **mpv** — media playback and IPC timing/control.
- **WLED** — DDP/network realtime protocol and controller behavior.

Evaluate what can be reused **without** distorting the desired architecture. In particular, determine whether FPP already offers a viable timecode-driven media/lighting backend or integration point. Record findings and license compatibility in `docs/alternatives.md`. Do not assume any third-party project's current capabilities without checking its documentation/source. This bootstrap does not assert that none of these projects can solve the problem.

---

## 13. Suggested repository layout

```text
reactive-show-engine/
├── README.md
├── BOOTSTRAP.md
├── LICENSE                     # only after license selection
├── pyproject.toml
├── compose.yaml                # Docker host deployment, when ready
├── src/
│   └── reactive_show_engine/
│       ├── core/               # domain models, rules, state machines
│       ├── controller/         # orchestration, APIs, event intake
│       ├── protocol/           # versioned agent messages
│       ├── timing/             # media timecode follower/estimator
│       ├── lighting/           # pure rendering, geometry, DDP
│       ├── media_agent/        # mpv adapter and telemetry
│       └── integrations/       # HA/MQTT/Frigate adapters
├── shows/
│   └── demo/                   # synthetic geometry, legal demo assets
├── examples/
│   ├── config.example.yaml
│   └── home_assistant/
├── docs/
│   ├── architecture.md
│   ├── protocol.md
│   ├── scene-schema.md
│   ├── synchronization.md
│   ├── operations.md
│   ├── alternatives.md
│   └── decisions/              # ADRs
└── tests/
    ├── unit/
    ├── integration/
    └── fixtures/
```

Treat this as a proposed layout, not a demand to create dozens of empty files. Keep the initial repo compact; split packages only when there is implementation behind them.

---

## 14. First milestones and acceptance criteria

### M0 — Architecture and project bootstrap

**Deliverables:** README, this spec, decision log, project skeleton, dev environment, CI, lint/type/test commands, and a short plan for M1–M3.

**Acceptance:** A clean clone can install dependencies and run a passing smoke test with documented commands. No hardware required.

### M1 — Authoritative timecode vertical slice (highest priority)

Implement a **fake media agent** that publishes deterministic playback position/state to a controller. Implement the Docker host-side timecode follower and a deterministic synthetic LED effect. Initially send frames to an in-memory/mock DDP sink, not physical LEDs.

**Acceptance:**

- Start a test scene through API.
- Fake agent reports positions with simulated jitter, pause, seek, and disconnect.
- Controller renders the correct pixels at the corresponding media positions.
- Automated tests cover duplicate/out-of-order telemetry, session fencing, bounded extrapolation, seek, pause, loop, and stale telemetry.
- Status endpoint reports active scene, current estimated position, and degraded state.

### M2 — Real media and lighting hardware

Implement laptop mpv agent and DDP output. Use a test media file with obvious visual flashes and sound clicks, plus an LED flash.

**Acceptance:**

- The media host plays video on the selected display and audio through the selected output.
- Laptop publishes actual player timecode; Docker host follows it.
- Docker host sends real DDP frames to WLED without modifying firmware.
- Calibrate and document measured alignment and jitter; no unsupported claim of frame accuracy.
- Stop/restart/failure restores or relinquishes WLED control safely.

### M3 — Event-driven interactive encounter

Add event API, rules, encounter state, cooldowns, priorities, and ambient fallback. Integrate a Frigate/HA-generated test event.

**Acceptance:**

- One detection initiates one encounter; repeated detections do not spam scenes.
- Sustained presence or porch-zone event can advance the encounter.
- A higher-priority operator command can interrupt or queue the encounter predictably.
- The system returns to ambient after completion/timeout.
- The same behavior can be tested without cameras via synthetic events.

### M4 — Example installation show package

Add installation geometry and media authored separately from engine code. Build example welcome, highlight, and ambient scenes. Keep copyrighted/private assets out of public examples.

### M5 — OSS hardening (later)

Documentation, onboarding, sample show, packaging, security review, cross-platform checks, protocol stability, license, contribution guide, and public release decision.

---

## 15. Testing strategy

### Pure unit tests

- Scene schema validation and resource binding.
- Encounter transitions, cooldowns, priority arbitration, idempotency.
- Timecode estimator under jitter, delay, packet loss, pause, seek, rate changes, loop, and out-of-order samples.
- Deterministic pixel rendering and geometry traversal.
- DDP packet segmentation/formatting and RGB/RGBW handling.

### Integration tests

- Fake agent ↔ controller WebSocket handshake, command/ack and reconnection.
- API-triggered scene lifecycle end to end using fake media and mock DDP.
- Stale telemetry and controller restart recovery.
- MQTT/HA event normalization and duplicate suppression.

### Hardware tests (explicitly opt-in)

- WLED DDP throughput and controller behavior on loss of stream.
- mpv monitor selection, fullscreen behavior, local media caching, and analog audio routing.
- Slow-motion flash/click synchronization measurement.
- Actual roofline pixel map verification and power/brightness constraints.

**No hardware tests should run in CI by default.** A simulation must be sufficient for contributor development.

---

## 16. Initial API and domain design principles

- Favor **event IDs, scene instance IDs, and playback session IDs** everywhere they help correctness.
- Prefer explicit state transitions and immutable event records over hidden side effects.
- Keep animation evaluation **pure** where practical: same inputs produce same pixels.
- Separate commands (`start_scene`) from events (`person_detected`) and from telemetry (`media_position`).
- Validate capabilities before scene execution; fail with actionable diagnostics.
- Preserve backward compatibility through versioned schemas/protocol messages when public use begins.
- Do not add a generic scripting engine before declarative scenes and rules prove insufficient.
- Prefer a few well-defined adapter interfaces to a large speculative plugin framework.
- Document all timing units, offsets, and clock domains.
- Use structured cancellation and explicit resource release on interruption.
- Design to tolerate an agent disconnecting or an external automation being unavailable.

---

## 17. Illustrative exhibit experience (creative example)

A fictional interactive exhibit uses an animated display and a light border. An optional second projection surface can extend the scene, while a configured camera zone qualifies visitor responses. Choose readable shapes, appropriate contrast, and viewing distances for the venue rather than assuming a particular building or projection arrangement.

Possible scenes:

- `ambient-display`: a quiet animated background with subtle accent lighting.
- `visitor-welcome`: an arrival makes a short welcome scene eligible.
- `display-highlight`: a visual accent follows an ordered LED path.
- `extended-visit`: sustained presence qualifies a longer scene variant.
- `display-shutdown`: a controlled power-down animation returns to the safe look.

Example timing, content, and transition choices belong in the show package. Test duplicate detections, scene boundaries, cooldown, visitor departure, and ambient recovery with synthetic observations before a physical rehearsal. These scenes illustrate engine capabilities, not a prescribed narrative or validated installation.

---

## 18. OSS positioning

**Potential positioning:** “A self-hosted, event-driven show engine that synchronizes media, lighting, and physical effects across distributed devices.”

Potential audiences: home Halloween/Christmas displays, haunted attractions, escape rooms, makers, small museums, interactive art, and home-theater ambient effects.

Differentiators worth validating:

- Stateful, externally triggered encounters instead of fixed playlists only.
- Playback-clock-driven real-time lighting.
- Distributed controller/agent model.
- Device-agnostic scene definitions and logical resources.
- Easy Home Assistant/Frigate integration without requiring either.
- Headless simulation and low-friction self-hosting.

Treat this as a **hypothesis about a useful niche**, not a proven market gap. Evaluate competing OSS projects and community needs. Consider MIT or Apache-2.0 after reviewing third-party license obligations. Do not publish proprietary or copyrighted media assets, private network configuration, or camera footage.

---

## 19. Decisions already made vs. preferences vs. open issues

| Topic | Status | Direction |
|---|---|---|
| Central orchestration on Docker host | **Decided** | Persistent self-hosted controller |
| Media host for video/audio | **Decided** | Configured display and audio output |
| DDP-compatible lighting controller | **Hard constraint** | Real-time network output, no firmware/hardware replacement |
| Playback position authoritative for synchronized lighting | **Decided** | Laptop telemetry → Docker host timecode follower |
| Docker host renders and streams DDP | **Preferred initial design** | Keep renderer relocatable in architecture |
| Home Assistant triggers | **Decided capability** | Generic event/API interface, HA adapter |
| Frigate person/zone interaction | **Primary reference use case** | Stateful encounters, no identity tracking |
| Python/FastAPI/mpv/WebSocket | **Preferred stack** | Validate platform compatibility |
| 10–20 Hz timecode, 20–30 FPS DDP | **Starting hypothesis** | Measure and tune |
| Exact sync offsets | **Unknown** | Calibrate with hardware |
| Media-host OS and installation pixel map | **Unknown** | Ask before hardware-specific work |
| Full composited media layers | **Future** | MVP can use transitions/clip replacement |
| Public OSS release | **Intention, not commitment** | Build cleanly now, decide after proof |

---

## 20. Immediate instruction to Copilot

**Begin with M0 and M1.** In your first response:

1. Summarize the architecture in your own words, explicitly distinguishing show-state authority from media-time authority.
2. Identify any blocking questions for the **software-only M1**; do not ask for projector model, house dimensions, or WLED IP yet if they are unnecessary.
3. Propose the smallest useful file structure and development commands.
4. Implement a thin controller, fake media agent, versioned telemetry contract, timecode follower, deterministic LED renderer, mock DDP sink, and end-to-end automated test.
5. Provide a concise `docs/decisions/` ADR for the media-clock-following choice and document any assumptions.
6. Run tests and report their actual results. Do not claim hardware validation until hardware tests have been performed.
7. Stop after a coherent vertical slice and suggest the next smallest increment; do not independently implement the entire roadmap in one enormous change.

**Key invariant:** When a media-backed scene is active, the lighting renderer follows the best bounded estimate of *actual media playback position*, not merely elapsed time since a controller-issued `play` command.
