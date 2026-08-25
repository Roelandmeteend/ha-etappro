# ha-etappro

Home Assistant custom integration (HACS) for the **ETAPpro EV charger** by
EVchargeking. Local Modbus TCP only — no cloud, no API key.

## Conventions

- **Write all code comments, docstrings, and log messages in English.**
  The repository is public, so English is the working language for anything
  developer-facing.
- User-facing entity names stay translated: `translations/nl.json` is Dutch,
  `translations/en.json` and `strings.json` are English. Keep the three files
  in sync — every translation key must exist in all of them.
- Bump the version in **both** `manifest.json` and `const.py` (`VERSION`) in
  the same commit; they must never drift apart.
- Semantic versioning: patch for bugfixes, minor for new behaviour or
  substantial internal rework, major for breaking changes.

## Layout

```
custom_components/etappro/
├── __init__.py       setup/unload, platform registration
├── config_flow.py    GUI setup wizard + options (polling interval)
├── const.py          domain, VERSION, register map, mode labels
├── coordinator.py    DataUpdateCoordinator, session latch, keepalive
├── modbus_client.py  raw Modbus TCP client (no pymodbus)
├── sensor.py         read-only sensors
├── number.py         current setpoint slider, phase count
├── switch.py         charging on/off
└── translations/     en.json, nl.json (+ strings.json at the same level)
```

## Hard-won details — do not regress these

**Register 1100 (hardware max current) can return milliamperes.** The vendor
docs say amperes, but at least one firmware returns e.g. `16000`. The client
treats any value above 100 as mA, since a charger realistically does 6–63 A.
This once made the setpoint slider range 6–16000.

**The Modbus stream desynchronises if errors are not separated.** The client
distinguishes two failure kinds and must keep doing so:

- `ETAPproRegisterError` — a Modbus *exception response*. The protocol was
  followed, the socket is clean: skip that register and continue.
- `ETAPproTransportError` — timeout, reset, or a transaction-ID mismatch.
  Unread bytes remain in the buffer, so every following read would parse the
  previous response as its header. **Abort the whole poll** and reconnect.

Continuing on a poisoned socket was the original bug: one hiccup cascaded
through all 27 register reads and polls took 12–36 s against a 10 s interval.

**Read contiguous registers in blocks** (`_BLOCKS` in `modbus_client.py`).
Seven requests per poll, not 27. A refused block falls back to individual
reads, and registers the firmware rejects are remembered and skipped.

**The charger accepts one Modbus connection at a time.** All socket use runs
behind a `threading.Lock` so a keepalive write cannot cut into a running poll.

**The charger has a watchdog** that reverts an externally written setpoint
after a timeout, so the coordinator re-writes the desired setpoint every poll.

## Session logic (coordinator)

HA control is scoped to an actual charging session, so RFID-card charging
(needed for employer reimbursement) keeps working:

| Charger mode | Meaning | Behaviour |
|---|---|---|
| `A` | free | release: clear setpoint, no keepalive, write nothing |
| `B` before any `C` | car plugged in, no session | write nothing |
| `C` | charging — started by RFID card or the HA "Charging" switch | session active: automations steer, keepalive holds the value |
| `B` after a `C` | pause within a session | stays active |
| back to `A` | car unplugged | release, ready for a fresh start by any means |

The latch turns **on** at mode C and **off** at mode A. Mode B never changes
it — that is what keeps HA from grabbing control the moment a car is plugged
in.

## Testing

There is no test suite in the repo. For Modbus client changes, run the fake
charger harness against a scratch copy: it verifies decoding, that a poll
takes 7 requests, that a stale response aborts instead of cascading, and that
unsupported registers are remembered. Import `modbus_client.py` directly via
`importlib` — importing the package pulls in `homeassistant`.

## Reference

Register map: https://github.com/EV-Chargeking/etap-modbus
Note that the documented units are not always what the firmware returns.
