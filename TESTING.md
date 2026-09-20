# Validation and limits

## Unreleased production engine candidate

Version 0.5.0.dev2 adds actual socket-level command tests against local Modbus
TCP, RTU-over-TCP and Solarman V5 emulators. The HA coordinator also reads a
complete synthetic Deye/Docan frame directly, without invoking a service or
writing to the emulator. Physical hardware was not contacted. This candidate
has not undergone a new HACS installation test.

The lifecycle tests cover startup admission, lost acknowledgements after a
command takes effect, malformed responses, disconnects, missing BMS data,
independent deadmen, a killed controller process, durable STOP across restart,
manual acknowledgement without automatic rearming, late thread commands after
cancellation, shared wire/serial ownership, settings attribution and storage
failure. See [the adapter test contract](CONTROL_ADAPTER.md).

Private offline comparisons also run the captured original modules against the
port. They compare 1,000 planner cases, 1,000 controller cases, 150 ceiling
decisions, 150 firmware schedule cases, 25 ordered write/failure cases, 20 reserve
admission cases and 10 recovery cases. Additional checks compare 138 assembled
daily plans, 12 matching plan refusals and 20 overnight histories. Differences in
sanitized explanatory wording are excluded from numerical comparisons.

These checks establish equivalence for the tested inputs, not all possible
operating conditions. See [the engine port](ENGINE_PORT.md) for missing live
equipment, supervision and commissioning work. The release evidence below refers
to the published 0.4.0-beta.1 unless a section states otherwise.

## Published beta

Release: **0.4.0-beta.1**, published 2026-09-20. Local testing used Home Assistant
2026.6.1, Python 3.14 and a headless Edge browser. No production HA system or
physical battery/inverter was contacted.

## Automated software checks

- 44 unit/protocol tests pass: three Deye transports, framing and bounded timeouts,
  fixed Docan telemetry/signs/checksums, forecast consent/cache/staleness, verified
  TLS without redirects, redacted real-client transport errors, energy
  conservation, an independent exhaustive optimizer oracle, tariff calculations,
  DST, infeasible plans, learning coverage and dedicated efficiency measurements.
- Official Home Assistant hassfest container passes on this integration.
- Unmodified upstream HACS manifest schemas pass locally, including negative
  cases. Local schema checks alone do not prove a downloaded HACS installation.
- Offline release verification checks metadata, translations, 196 bundled
  dependency files, prohibited services/control platforms and private-file exclusions.
- Recursive privacy scanning includes nested bundled source archives and the ZIP;
  findings/dispositions are in SANITIZE_REPORT.md.

Reproduce the software checks in an isolated environment:

```sh
python -m pip install -r requirements-dev.txt
python -m unittest discover -s tests -v
python tools/verify_release.py
```

## Disposable HA simulation

Two separate local HA instances exercised native config flows with a simulated
Deye TCP endpoint and Solarman V5 endpoint, plus a real OS pseudo-terminal and
pyserial for the simulated Docan reader. Starting without equipment sensors,
setup created the sensors and dashboard and produced a forecast schedule.

Failure cases covered unavailable battery, inverter and solar forecast inputs;
the plan was hidden and recovered after valid input returned. Reload, private
settings restore into a second instance, identity preservation and removal were
also exercised, including learned-store persistence on reload and removal with
the entry. Desktop and 390-pixel phone layouts were checked. Requests remained
fixed telemetry reads: Modbus function 03 and
the fixed Docan query. No domain services were registered. Browser checks covered
power flow, history/price/forecast canvases, JavaScript errors and layout overflow.

Only synthetic prices, locations and measurements were used. The forecast API
response was simulated; the real reader/cache/conversion path ran, but this is
not a live Forecast.Solar, Tibber or Nord Pool availability test. Map networking
was disabled. These simulations do not validate physical wiring, firmware,
RS485 bus coexistence or operation under real long-running household conditions.

## Published-release checks

- The owner-authorized public repository and GitHub prerelease are published.
- [Hassfest](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/runs/35501417343),
  [HACS validation](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/runs/35501417661) and
  [software tests](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/runs/35501417330) passed on release commit
  `7c978e341fdca2f697a3dd4b8d0b7d07cf9e2a81`.
- The downloaded release ZIP matches the checksum attached to the prerelease.

## Actual HACS installation verification

On 2026-09-20, an additional disposable HA 2026.6.1 instance used unmodified
HACS 2.0.5. The repository was added through HACS's Custom repositories UI as
an Integration, then `0.4.0-beta.1` was explicitly selected and downloaded.
The first GitHub device registration attempt failed; a normal UI retry and
owner authorization succeeded. No HACS storage or authentication was injected.

The approximately 58 MB source archive exceeded HACS's 60-second download
timeout on this host. HACS exhausted its normal retries, then its built-in
file-by-file fallback completed. All 224 component files matched the published
release; the five gzip files HACS created decompressed to the same source bytes.

After a disposable HA restart, native HA REST config-flow setup against
synthetic Deye TCP and Docan pseudo-terminal peers produced 31 read-only
entities, an automatic dashboard and an `estimate_ready` forecast schedule.
The browser rendered power flow, battery readings and the forecast timeline.
No integration JavaScript errors were observed. The optional map was disabled.
Three learning/accuracy entities correctly remained unavailable without history.

Zero integration-domain services were registered. Observed equipment traffic
contained 90 Modbus function-03 reads and 10 copies of the fixed Docan telemetry
query. Reload preserved readiness, the panel and learning store. Native HA
config-entry removal removed its entities, panel and learning store. HACS's
downloaded package files correctly remain until a separate HACS uninstall.
No production HA host or physical equipment was accessed.

## Remaining installation and field gates

- A genuine version-to-version upgrade test awaits a subsequent public release.
- Physical verification is intentionally deferred. All hardware variants retain
  their experimental status; no claim of field certification is made.
- Multi-orientation solar needs an external aggregated forecast sensor. Learning
  needs real history; cost reports are not proven savings.
- The all-in-one software package still needs the physical adapters, commissioned
  equipment, site configuration and any optional external measurement sources
  documented in README.md.
