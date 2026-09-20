# Release acceptance

The release is complete only when the implementation and verification below
cover the installed Home Assistant product. Passing library tests alone is not
completion. Physical field evidence must be distinguished from simulation.

| Requirement | Evidence required |
|---|---|
| HACS installation | Install the release ZIP in disposable HA through HACS, set up, reload, upgrade and remove |
| Hardware setup | Default Panda 32 kWh and Deye 10 kW; bounded 6/8/12 kW profiles; direct inverter and independent BMS connections |
| Household setup | User-entered location, solar capacity/topology and price-provider credentials; no personal defaults |
| Electricity pricing | Nord Pool plus household taxes/fees, Tibber, import and export tariffs kept separate |
| Planning | Daily charge/export decisions, conditional ceiling, reserve history, cold start, overnight arming and morning update |
| Calendar | Correct actual delivery intervals, including short and long DST days; firmware wall-clock restrictions explicit |
| Operating modes | Starts read-only; shadow comparison; explicit commissioning and live activation; stop and return to observation |
| Commissioning | Device identity, firmware, profile, measured inputs, wiring/sign confirmation, sole-writer confirmation and bounded settings |
| Execution | All six firmware programs, ordered controls, readback, persistent intent and STOP; no automatic restart after uncertainty |
| Independent guards | Charge/export, settings auditor and heartbeat workers survive a controller failure; unload and host limits covered |
| Owner visibility | Dashboard shows actual mode, decisions, schedule, commissioning, STOP and errors; local HA notifications |
| Backup/restore | HA backup preserves state; portable import excludes credentials and never restores live authority |
| Privacy/licensing | Recursive scan of all public bytes and ZIP; dependency licenses; MIT; no private identity or production endpoints |
| Publication | Final documentation, green CI, reviewed main commit, beta release and matching downloadable artifact |

Testing must use local emulators and a disposable HA instance. No operating
installation may be contacted as part of this release work. Software validation
does not establish physical compatibility for every inverter/BMS firmware.
