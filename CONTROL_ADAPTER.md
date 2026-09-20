# Control adapter and local validation

Version **0.5.0.dev2** is a development candidate. The Home Assistant integration
only observes and simulates. Command transport rejects every non-loopback
destination, independently of the session's authority checks. There is no HA
live-mode toggle, control service or automatic commissioning.

## What the adapter does

`DeyeDevice` reads a complete frame: telemetry, control settings, all six TOU
programs, battery alarm, device alarms and faults, battery operating mode and
TOU weekday flags. It supports Modbus TCP, RTU-over-TCP and Solarman V5 framing.
Requests have bounded lengths and timeouts. Reply identities, functions, lengths,
CRC/checksum and write echoes are checked before using data.

The independent Docan reader supplies pack current/voltage, SoC, cell spread,
cell-voltage sum, MOS temperature, environment temperature and four probes.
Unsupported probe layouts fail closed. Controller admission needs three
consecutive coherent frames; missing data is never replaced with a healthy value.

With direct Deye and Docan USB selected, HA's production shadow policy uses this
frame itself. Existing-sensor setups can still supply a complete frame. Normal
telemetry and controller observations share one poll, including the BMS read.
The direct grid value comes from the Deye grid-power register. It has not been
shown to match an independent fiscal meter on a real installation.

## Register contract

These decimal addresses and encodings follow the LV branch of the upstream
[Solarman Deye three-phase definition](https://github.com/davidrapan/ha-solarman/blob/main/custom_components/solarman/inverter_definitions/deye_p3.yaml).
That definition is protocol evidence, not commissioning evidence for a particular
SG05LP3 firmware. No upstream implementation was copied into the command adapter.

| Field | Register | Encoding |
|---|---:|---|
| Battery operating mode | 111 | 0 voltage, 1 capacity, 2 absent; read only |
| Grid charge current / enable | 128 / 130 | A / off-on |
| Energy pattern | 141 | 0 battery first, 1 load first |
| Work mode | 142 | 0 export, 1 zero-export load, 2 zero-export CT |
| Export limit / solar sell | 143 / 145 | W / off-on |
| TOU | 146 | Enable bit 0; weekday bits preserved |
| Six program starts | 148–153 | HHMM |
| Six program power limits | 154–159 | W |
| Six program voltages | 160–165 | 0.01 V |
| Six program SoC limits | 166–171 | % |
| Six charging selectors | 172–177 | Disabled, grid, generator, both |
| Battery alarm | 220 | Nonzero is an alarm |
| Grid export limit | 231 | 10 W |
| Device alarms / faults | 553–558 | Any nonzero value rejects healthy status |

Read requests use function 03. Lab commands use function 16 with one register,
then an independent readback. The wire format follows the
[Modbus application specification, section 6.12](https://www.modbus.org/file/secure/modbusprotocolspecification.pdf).
Solarman envelopes follow the
[V5 protocol description](https://pysolarmanv5.readthedocs.io/en/latest/solarmanv5_protocol.html).
Actual firmware acceptance of these commands remains unverified.

## Execution and STOP

The lab interface accepts named fields, with explicit bounds and resolution.
It has no register editor. A session must acquire exclusive ownership, collect
valid observations and be explicitly armed. It starts disarmed after a clean
restart. Persisted activity at restart causes a STOP latch and minimal reductions.

SQLite stores intent, verified outcomes, state and the STOP latch with full
synchronization. Commands are never retried after a missing acknowledgement or
unverified readback. A shared wire lock orders controller and watchdog traffic.
Command authority is checked again after taking that lock, immediately before
sending. Another process's STOP revokes queued commands. Serial readers also
share ownership; cancelling an asynchronous call does not release ownership
while its serial thread is still running.

The six-program transaction requires fresh idle measurements and inactive
controls. It disables TOU and charging selectors, stages every bounded field,
verifies the complete block and restores the previous TOU enable state only on
success. Failure latches STOP and never restores prior activity. Weekday bits
are retained. Duplicate or unproven midnight boundaries are refused.

Minimal STOP attempts solar-sell off, grid-charge off, zero-export-to-CT and
load-first, including when another reduction fails. Storage failure does not
suppress these reductions, but the result reports that the durable record failed.
Register readback is recorded separately from physical completion. It never
claims that electricity flow has stopped.

Acknowledgement needs the current latch ID, owner STOP cleared, three fresh
observations, healthy alarms, inactive controls and idle power readings. It
clears the reviewed latch but remains disarmed. Activation is a separate step.
A controller thread still running after cancellation blocks acknowledgement.

## Independent watchdogs

Charge, export, supervisor and auditor workers use the durable store and their
own adapter. They do not depend on planner prices. Each role has an OS-backed
ownership lock. Charge/export workers obtain fresh equipment observations. The
supervisor checks controller and watchdog heartbeats. The auditor accepts actual
verified outcomes as attribution; an intent or a shadow proposal is insufficient.
Worker loops require an explicit installation-local, timezone-aware clock for
the export cutoff. They do not silently choose a server timezone.

The auditor detects unexplained *setting changes*. Polling cannot detect an
outside writer that writes the same value or changes and restores it between
polls. Exclusivity only covers participating processes sharing the same state
directory. It cannot exclude a second EMS, the inverter app or another computer.

The process test starts separate planner and supervisor interpreters, terminates
only that test planner and verifies that the supervisor stops the emulator. This
proves independence from that planner process. It does not prove safety after an
HA host failure, network loss or power outage. Firmware or a separate protection
system must cover those failure modes before live use.

## Reproduce the tests

Use an isolated development environment with `requirements-dev.txt` installed:

```sh
python -B -m unittest discover -s tests -p 'test_control*.py' -v
python -B -m unittest discover -s tests -p 'test_engine_adapter.py' -v
```

Tests bind loopback sockets and use synthetic BMS frames. They neither discover
devices nor accept production credentials. Faults include invalid frames,
timeouts, disconnects, lost replies after applied commands, refused writes,
controller death, cancellation, conflicting changes, missing telemetry, storage
failure, stale plans, DST days, restart and manual STOP review.

## Release boundary

The software needed for local execution and fault tests is present. The live HA
lifecycle, notifications, per-installation calibration, tariff generalization,
DST support and independently verified physical commissioning remain release
requirements. The reference profile is limited to the 10 kW model. This branch
must not be advertised as a production-ready live EMS.
