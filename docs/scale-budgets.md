# GUI scale budgets

Cueverge qualifies the production GUI against the following per-installation
budgets. These are supported operating limits, not recommended authoring
targets.

| Surface | Supported budget | Interactive target |
| --- | ---: | ---: |
| Scenes in one project | 250 | Save and compile within 5 seconds |
| Timeline items in one composition | 10,000 | Open within 5 seconds; bounded canvas and editor DOM |
| Assets indexed in one project | 5,000 | Open within 5 seconds; fewer than 20 rendered rows |
| Connected device clients | 100 | Registry update within 2 seconds; health visible within 5 seconds |
| Installation reference photos | 12 | Viewpoint switching within 1 second |
| Deterministic simulation | 100 steps | Complete within 5 seconds with no hardware adapters |
| Publication | 250 scenes | Compile and publish within 5 seconds each |

Automated qualification compiles and publishes the maximum scene budget, runs a
100-step no-hardware simulation, connects 100 in-memory device sessions, loads
12 mapper viewpoints, and exercises 10,000 timeline markers and 5,000 assets
through bounded browser DOM windows. Production profiling must be repeated on
the deployment-class controller before increasing any limit. Operations should
alert when an installation reaches 80 percent of a budget so it can be split or
archived before authoring becomes unreliable.

The observability endpoint at `/api/gui/v1/observability` is restricted to
administrators and reports bounded API latency samples, HTTP outcomes,
background and asset job states, device WebSocket health, browser errors,
storage capacity, simulation runs, and publication states. Browser errors are
also attributed in the durable audit log. Metrics are intentionally bounded in
memory; an external collector should scrape the endpoint and retain long-term
history.
