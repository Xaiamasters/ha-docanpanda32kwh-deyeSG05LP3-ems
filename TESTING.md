# Validation and limits

Version **0.5.0-beta.2** includes live control after commissioning. Validation
uses Python 3.14, Home Assistant 2026.6.1, a disposable local HA instance, a
headless Edge browser, owned loopback inverter emulators and an OS pseudo-terminal
for the Docan reader. No operating household equipment is used by these tests.

## Software coverage

- Modbus TCP, RTU-over-TCP and Solarman V5 reads/writes with response identity,
  length, checksum, function, echo, readback, disconnect and timeout cases.
- No retry after a write may have taken effect; durable intent before writes;
  ordered reductions and persistent STOP across failures/restarts.
- Independent complete battery observations, coherent voltage, temperatures,
  admission warmup and missing-input refusal. No invented healthy values.
- Original planning/controller behaviour and deliberate public extensions:
  separate tariffs, bounded inverter profiles, first-install assumptions,
  missed scheduled jobs and 23/24/25-hour delivery days.
- Controller cancellation, late thread commands, shared serial/wire ownership,
  changed firmware, stale approval, lost guards and duplicate device ownership.
- Real guard processes starting, terminating and stopping a simulated inverter
  when the parent controller heartbeat disappears.
- HA configuration, persistence, source renaming, price providers, strict admin
  HTTP operations, redacted errors and portable settings without credentials or
  command authority.
- Forecast energy conservation, an independent exhaustive optimization oracle,
  forecast consent, historical price anticipation and measured-efficiency rules.
- Causal forecast vintages, P50/P80 loss units, complete-day hindsight scoring,
  paired schedule replays, AC/DC solar attribution, missing coverage and DST.
- Reporting entities in automatic mode and setpoint invalidation on STOP.

The current test count and full result are recorded by the
[Software tests workflow](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/tests.yaml).
Hassfest and HACS validation have separate workflows. The package verifier checks
metadata, translations, bundled dependency hashes, private-file exclusions and
the absence of general HA control services/register editors.

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/verify_release.py
```

The POSIX process/serial test needs a writable `/dev/serial/by-id` fixture
directory. It creates a uniquely named link to its own pseudo-terminal and removes
only that link. It skips on systems without this permission. CI prepares this
directory in its disposable runner; ordinary unit tests need no serial device.

## Installed Home Assistant test

The candidate is installed in disposable HA and configured using its native
configuration-flow API. Its frontend then performs the actual commissioning,
live activation, stop and acknowledgement requests through the dashboard.
The inverter/BMS endpoints are synthetic, but the HA integration, HTTP routes,
serial reader, controller, store and guard processes are the shipped code.

Checks cover the automatic sidebar dashboard and power-flow card, desktop and
390-pixel mobile layouts, console errors, all six firmware programs, actual mode
reporting, disarmed acknowledgement, reload, private settings restore and entry
removal. The configuration starts without pre-existing equipment sensors.

Release installation through HACS is a separate check from file-copy setup.
Exact release-download hashes and installation evidence belong in the release's
validation attachments. A green HACS schema workflow alone does not prove an
installed integration, and an earlier beta's install does not prove a later one.

## Source comparison and remaining physical evidence

The initial engine extraction was compared offline with saved source modules:
1,000 planner and 1,000 controller cases, 150 ceiling choices, 150 firmware
schedules, 25 ordered transaction/failure cases, 20 reserve cases, 10 recovery
cases, 138 assembled daily plans, 12 refusals and 20 overnight histories.
Household tariff, calendar and profile extensions have separate regression
tests; the public product is not claimed to reproduce a private site's values.

Passing software tests does **not** verify the exact wiring, firmware register
semantics, current/CT signs, meter accuracy, physical stop time or thermal limits
of a user's installation. Rated model and firmware identity checks prevent
silent substitution; they do not replace physical commissioning evidence.
The independent processes also cannot survive complete host/container failure
as functioning equipment guards. See [control limits](CONTROL_ADAPTER.md).

Public bytes are scanned recursively, including nested source archives and the
release ZIP. Findings and dispositions are recorded in [SANITIZE_REPORT.md](SANITIZE_REPORT.md).
