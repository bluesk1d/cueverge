# Autonomous show readiness roadmap

This roadmap turns the first-show requirements into generic Cueverge engine
capabilities. It preserves three independent layers:

1. **Observation** converts external messages into trustworthy audience state
   and expiring engagement opportunities.
2. **Show direction** chooses what should play next from declarative
   programming, history, priorities, and opportunities.
3. **Presentation** executes one selected scene using synchronized media and
   deterministic lighting.

Installation narratives, private geometry, camera names, and media remain outside
the reusable engine.

## Progress

| Feature | Status |
|---|---|
| CV-01 Automatic scene lifecycle | Implemented |
| CV-02 Declarative show director | Implemented |
| CV-03 Aggregate audience presence | Implemented |
| CV-04 Safe transitions and request arbitration | Implemented |
| CV-05 Engagement opportunities | Implemented |
| CV-07 Semantic lighting geometry | Implemented |
| CV-06 Multi-cue deterministic lighting | Implemented |
| CV-08 Persistent lighting looks | Implemented |
| CV-09 Operations, simulation, and observability | Implemented |

## Milestone 1: continuous autonomous programming

### CV-01: automatic scene lifecycle (P0)

Add playback-session-fenced completion policies:

- `loop`
- `transition_to`
- `select_next`
- `hold`
- `stop`

Scenes may declare a failure fallback for prepare errors. Duplicate completion
telemetry must not advance more than once. Completion and failover counts must
be observable.

**Acceptance:** loops, explicit transitions, director selection, hold, stop,
and prepare fallback are automated and tested. Controller restart never adopts
or advances an unknown completed playback session.

### CV-02: declarative show director (P0)

Add show programming with scene pools, weights, eligibility, cooldowns,
recent-history exclusions, and categories. Selection occurs at completion
boundaries through the CV-01 selector interface.

**Acceptance:** a deterministic simulated 30-minute run operates unattended,
avoids immediate repetition, respects cooldowns, and skips unavailable scenes.

### CV-03: aggregate audience presence (P0)

Track relevant person observations using configurable cameras/zones,
freshness, arrival confirmation, and absence grace periods. Emit semantic
arrival, presence, departure, and long-idle changes without identifying or
counting individuals.

**Acceptance:** overlapping observations form one audience presence; tracking
gaps do not cause false departure; distant traffic can be excluded; sustained
absence returns to idle.

### CV-04: safe transitions and request arbitration (P0)

Extend the director request boundary with safe cut points, protected
intervals, expiration, coalescing, and interruption timing:

- `immediate`
- `at_safe_point`
- `after_completion`
- `ignore`

CV-04 follows CV-02. It operates on generic requests; CV-05 opportunities can
submit requests later without changing arbitration semantics.

**Acceptance:** protected scenes finish, expired requests never run, repeated
requests coalesce, operator commands remain authoritative, and stale reactions
cannot build a backlog.

## Milestone 2: interactive performance and expressive lighting

### CV-05: engagement opportunities (P0)

Create time-limited opportunities from audience context, including sustained
presence, return after absence, recent zone activity, and acknowledgement
cooldowns. Opportunities are consumed once at eligible boundaries or expire.

**Acceptance:** opportunities neither interrupt protected scenes nor fire
twice, and every interactive branch has an autonomous fallback.

### CV-07: semantic lighting geometry (P0)

Define installation-owned fixtures, overlapping groups, and ordered paths
using validated pixel segments. Support local reversal and explicit precedence.

**Acceptance:** scenes target semantic fixtures and architectural paths without
embedding pixel indices in show logic.

### CV-06: multi-cue deterministic lighting (P0)

Add timecoded cue tracks and deterministic primitives:

- solid and blackout
- envelope
- pulse
- chase
- path sweep
- seeded flicker
- seeded sparkle
- ripple
- color transition

Support explicit blend modes, precedence, and output brightness limits.

**Acceptance:** a scene combines a baseline, localized charge, multipath
traveling pulse, whole-roof flash, and fade while remaining deterministic
under pause, seek, and reconnect.

## Milestone 3: continuity and operations

### CV-08: persistent lighting looks (P1)

Add show-level and fixture-level baseline looks. Scene cues temporarily
override or blend with baselines and restore them without unintended black
frames. Stop can force a configured safe state.

### CV-09: operations, simulation, and observability (P1)

Expose show mode, active/next programming, audience state, opportunities,
selection history, cooldowns, adapter health, manual controls, blackout, and
maintenance mode. Add deterministic synthetic observations, accelerated time,
repeatable scheduling, and simulated lighting.

**Acceptance:** a reproducible 30-minute scenario covers overlapping arrivals,
sustained attendance, tracking gaps, absence, protected scenes, opportunities,
operator overrides, and restart behavior without hardware.

Implemented with automatic, disabled, maintenance, and blackout modes;
capability-aware status diagnostics; privacy-preserving observation history;
director eligibility diagnostics; and an in-memory accelerated simulation
harness. The acceptance scenario runs without constructing MQTT, mpv, DDP, or
other physical-output adapters.

## Dependency order

```text
CV-01 -> CV-02 -> CV-04
CV-03 -> CV-05
CV-07 -> CV-06 -> CV-08
CV-02 + CV-03 + CV-04 + CV-05 + CV-06 + CV-07 + CV-08 -> CV-09
```

CV-03 and CV-07 can proceed in parallel with the director work. Basic
simulation fixtures are added with each milestone; CV-09 consolidates them
into the operator-facing simulation surface.

## Deliberately deferred

- Real-time video compositing and frame-locked playback without hardware
  synchronization.
- Facial, gesture, or individual visitor recognition.
- Accurate crowd counting.
- Mid-clip video branching.
- Independently mixed sound-effect channels.
- Fog, motors, relays, and other irreversible physical effects.
- Complex cross-camera spatial tracking.

## First-show implementation gate

Final media production can proceed with confidence after CV-01 through CV-07
pass an end-to-end placeholder test:

1. Quiet ambient programming runs continuously.
2. Presence after a long idle makes an awakening scene eligible.
3. Engagement follows at a completion boundary.
4. Arrivals during a protected spectacle do not interrupt it.
5. Activity in an example interaction zone creates one expiring welcome opportunity.
6. Media-time lighting targets semantic display-border and accent fixtures.
7. Ten minutes of attendance rotates content without immediate repetition.
8. Sustained absence returns gracefully to quiet ambient.
9. Controller and agent interruptions do not duplicate completion or
   engagement transitions.
