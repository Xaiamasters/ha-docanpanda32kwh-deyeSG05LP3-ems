# Validation and limits

Candidate: **0.4.0-beta.1**, staged 2026-09-20. Local testing used Home Assistant
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
  cases. This is not the GitHub HACS Action or a downloaded HACS installation.
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

## Remaining publication and field gates

- Owner publication decision, actual GitHub Actions, release and downloaded HACS
  install/upgrade verification remain outstanding.
- Physical verification is intentionally deferred. All hardware variants retain
  their experimental status; no claim of field certification is made.
- Multi-orientation solar needs an external aggregated forecast sensor. Learning
  needs real history; cost reports are not proven savings.
- The all-in-one software package still needs the physical adapters, commissioned
  equipment, site configuration and any optional external measurement sources
  documented in README.md.
