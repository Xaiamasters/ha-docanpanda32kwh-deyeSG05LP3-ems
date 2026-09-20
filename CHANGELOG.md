# Changelog

## 0.5.0.dev1 (unreleased)

- Port the production price planner, conditional charge ceiling, measured reserve,
  six-program conversion, controller decisions and verified-write transaction core.
- Add a separate production-policy shadow adapter with persistent local state.
- Compare the port with the original source offline and test write failures using
  an in-memory device. No physical equipment adapter or live-mode switch is added.
- Keep the forecast optimizer as a separate selectable policy. See ENGINE_PORT.md
  for reference-profile limits and the remaining live-control work.

## 0.4.0-beta.1: first public beta, 2026-09-20

- Public identity: Docan Panda & Deye EMS, integration domain `docan_deye_ems`.
- Independent Docan USB/RS485 telemetry with fixed read query and no command API.
- Solar forecast input, learned local load profile and optional anticipated prices.
- Read-only energy optimization with export compensation, losses, wear, reserve,
  deadline and import/export/power constraints.
- Optional efficiency learning from dedicated AC/DC battery measurements.
- Forecast and observed-cost entities, dashboard graphs and coverage reporting.
- Private local learning storage, settings export/import and explicit provider consent.
- Bundled dashboard and licensed dependencies; no separate card installation.
- Unit tests and disposable HA simulations; no physical hardware certification.

This domain is a separate install, not an in-place migration of another private
integration. This is the first public prerelease for this separate domain.
