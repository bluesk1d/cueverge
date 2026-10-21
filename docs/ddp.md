# DDP output

The controller includes an opt-in unicast DDP v1 UDP sink:

- UDP port 4048 by default.
- Ten-byte headers with 32-bit channel offsets and 16-bit payload lengths.
- Four-bit packet sequence and push flag only on the final packet.
- RGB payload segmentation only on complete pixel boundaries.
- No optional DDP timecode because WLED does not consume it.

`CUEVERGE_DDP_ENABLED` defaults to false. Enabling it requires an explicit host
and expected pixel count. RGB channel values are sent unscaled by default.
`CUEVERGE_DDP_BRIGHTNESS_LIMIT` can optionally scale output for artistic or
installation-specific reasons; Cueverge does not treat it as an electrical
safety control.

The legacy environment settings configure one DDP sink. Deployments with
multiple logical pixel resources set `CUEVERGE_OUTPUT_CONFIG` to a private JSON
file instead:

```json
{
  "schema_version": 1,
  "lighting": [
    {
      "resource": "roofline",
      "kind": "ddp",
      "host": "roofline-wled.local",
      "expected_pixel_count": 605
    },
    {
      "resource": "porch",
      "kind": "ddp",
      "host": "porch-wled.local",
      "expected_pixel_count": 150,
      "brightness_limit": 0.6
    }
  ]
}
```

Resource names must be unique and must cover every lighting resource used by
the loaded show. The controller rejects simultaneous use of
`CUEVERGE_OUTPUT_CONFIG` and the legacy `CUEVERGE_DDP_*` settings. See
`examples/output-config.example.json` for every available field. Keep real
addresses and installation limits outside the repository.

## Physical maintenance chase

Configured DDP outputs with matching installation pixel counts can run a
controller-driven maintenance index chase without a test scene or publication.
Use **Operate > Physical LED output tests** or the physical-test controls in a
mapper traversal preview. Start requires operator/administrator access, CSRF
validation, idle maintenance mode, and exact resource-name confirmation.

The test follows increasing physical output indices using a five-pixel pulse,
with saved mapper segment colors or a plain white pulse. Maintenance tests target
60 frames per second, independently of the show's render rate. Fractional pixel
positions crossfade adjacent LEDs instead of jumping between whole indices;
segment colors remain tied to each LED's allocation. Missed deadlines skip stale
frames rather than sending catch-up bursts. Actual smoothness also depends on
network delivery and the device's refresh rate. Existing DDP
`brightness_limit` settings remain authoritative; there is no additional
maintenance-test brightness cap or automatic downstream reconfiguration.
Mapped tests reject invalid/overlapping saved allocations. Unmapped indices
remain in the traversal and use white.

The runtime owns the test, enforces its 5–30-second API duration, and excludes
scene starts while testing. Closing a browser does not cancel it. Explicit
stop/disarm, mode changes, global Stop, shutdown, errors, or timeout end the
session. A final black frame is attempted on the selected output before
releasing realtime transmission; failures are visible in test and runtime
status and logged. Downstream realtime timeout/preset behavior is unchanged.

Authenticated GUI endpoints are `GET/POST /api/gui/v1/maintenance/output-tests`
and `POST /api/gui/v1/maintenance/output-tests/{id}/stop`. The existing
`test-pattern-preview` endpoint remains simulation-only. Start and terminal
test events are audited. UDP send success is not a device acknowledgement.

Before enabling output, validate:

- WLED version, configured LED count, RGB/RGBW layout, and color order.
- Unicast reachability from the NAS and sustainable frame rate.
- Realtime ownership and behavior when packets stop.
- How to restore or relinquish to the prior WLED preset.

The downstream controller and physical installation own electrical
provisioning, protection, and safe operating limits. Cueverge assumes an
enabled output target has already been configured appropriately.

RGBW output is not implemented. Do not enable the sink for an RGBW installation.

## 2026-10-08 hardware spike

Read-only inspection and an authorized controlled test established:

- WLED 0.16.0.1 on ESP32 with 605 RGB pixels.
- Two physical outputs: 377 and 228 pixels, represented as one linear range.
- DDP input accepted all 605 pixels and identified the realtime source as DDP.
- A dim three-pixel frame at 20 FPS was received successfully.
- 300 all-black frames were sent at exactly 30.00 FPS over ten seconds with
  zero sender deadline misses.
- WLED relinquished realtime mode within five seconds and returned to the exact
  prior off state after both tests.

WLED's instantaneous JSON FPS field reported values different from commanded
cadence and is treated as diagnostic only. Packet loss and visible update
latency were not measured.
