# Production engine port

This is the **0.5.0.dev2 development candidate**. It includes the planning and
controller policy extracted from an operating installation, with household
identifiers and external access removed. Earlier source comparisons used a
bounded read-only capture. This adapter phase used saved files and local tests;
it did not contact an operating HA system.

HA runs the policy in shadow mode. The command adapter can exercise it against a
loopback emulator. **Physical command destinations are rejected.** The code and
tests are available for review; this is not a production control release.
See [adapter details and commissioning requirements](CONTROL_ADAPTER.md).

## What is implemented

| Part | Behaviour in the candidate |
|---|---|
| Price planner | Uses 96 published quarter-hour prices, a tapered charging curve, chronological charge selection and evening export clusters |
| Charge target | Starts at 90%; uses 95% only when the added energy fits, pays and has enough remaining export opportunity |
| Overnight reserve | Measures the overnight trough, rejects incomplete nights, limits outliers and reserve changes, and distinguishes an unusable measurement day from an excessive reserve number |
| Daily schedule | Simulates the 23:15 next-day arm and 10:30 morning re-pin using the HA timezone; does not shift missed jobs into startup |
| Deye programs | Converts the plan to six firmware boundaries and applies the original schedule checks |
| Controller | Calculates CHARGE, HOLD, IDLE or EXPORT using the observed firmware window, pinned reserve/ceiling, temperatures, freshness, alarms and owner-stop inputs |
| Transaction core | Orders changes, records intent before a simulated write, checks readback, retries reads without repeating commands, and retains a STOP latch after uncertain writes |
| Deadmen and recovery | Independent charge/export, heartbeat and settings-auditor workers with durable STOP recovery, tested locally; HA live supervision and notifications are not enabled |
| Home Assistant | Production shadow mode reads direct Deye/Docan observations or a supplied frame, with local persistence, input errors and plan details |

The existing forecast optimizer remains a separate policy. It uses solar/load
forecasts and can estimate unpublished prices. Those features do not influence
this production-policy port. The operating source keeps solar forecast accuracy
as a separate observation task; it is not an input to its active price planner.

## Reference profile and pricing limits

The captured control profile is for a 32 kWh-class battery with the **10 kW Deye**.
The port preserves its engineering constants and guard rules for comparison,
including the measured taper profile, 55.2 V charge/HOLD setting, 160 A charge
setting and 8 kW active-program limit. They have not been commissioned for other
equipment or firmware. Selecting this comparison policy for 6, 8 or 12 kW models
is refused. Their existing read-only telemetry profiles are unaffected.

The setup form exposes capacity, fallback reserve, export power, round-trip
efficiency, wear cost and the controller's export-value deduction. No household
tariff deduction is supplied: its default is zero and the installer must enter
the applicable value. These inputs are simulation assumptions, not equipment
permissions.

The original policy uses one EUR price curve for selecting charge/export slots
and a deduction in the controller's marginal export-value calculation. The
separate fixed/curve export tariff used by the forecast optimizer does **not**
apply to this mode. Generalizing that tariff contract consistently across daily
planning, ceiling selection and control remains a release requirement.

The port rejects 23-hour and 25-hour delivery days. The original 96-slot policy
must be changed and tested before it can support daylight-saving transitions.

## Shadow comparison input

Select **Production policy comparison** during plan setup. With direct Deye
and independent Docan USB configured, the integration reads its own frame.
Otherwise supply a local sensor carrying this contract in its attributes:

- `schema`: integer `1`.
- `updated_at`: timezone-aware timestamp of the captured frame.
- `snapshot`: the fields below, all from one coherent observation.
- `day_plan`: optional current-day pin with `for_date`, `ceiling_pct`,
  `export_clusters` as `[start_slot, end_slot, reserve_pct]`, and `reserve.pct`.
- `overnight_draws`: optional measured history artifact, with `for_date` and
  `reserve` containing `pct`, `n`, `p50` and `p90`. It is never silently re-dated.

| Snapshot fields | Meaning |
|---|---|
| `soc`, `pack_v`, `p1_w` | Battery SoC, pack voltage, and fiscal grid power; positive grid power means import |
| `grid_charge_a`, `export_w`, `grid_export_w` | Observed inverter control settings |
| `grid_charge_on`, `solar_sell_on`, `stop`, `discharge_now`, `pause_charge` | Observed booleans, not commands |
| `docan_fresh`, `truth_ready` | Independent battery-source validity flags |
| `tou`, `work_mode`, `energy_pattern`, `alarm`, `batt_alarm` | Actual firmware/health states; healthy alarms are `OK` and `off` |
| `temps` | `mos_temperature`, `environment_temperature`, and `probe_1_temperature` through `probe_4_temperature`, in Celsius |
| `ages` | Ages in seconds for `docan`, `deye` and `p1`; transport delay is added before checking them |
| `programs` | Six entries keyed `1` through `6`, each with `time`, `charging`, `soc`, `power` and `voltage` |

Missing values must remain missing. Do not fill alarms, temperatures or freshness
with assumed healthy values. Synthetic fixtures in the tests are for tests only.

The direct reader requires the supported 16-cell/four-probe BMS frame, coherent
pack/cell voltage and three consecutive valid observations. A failed read resets
admission. It also checks voltage mode and today's enabled TOU bit. Its grid
input comes from the inverter; equivalence to an independent fiscal meter is
not established. Shadow comparison never interprets that as write permission.

Up to 14 days of observations are retained in private HA storage. Rebinding
equipment or changing comparison settings invalidates prior engine state.
Daily planning needs measured overnight history. A new installation cannot
reconstruct a previous household's reserve history or pinned day plan.
No endpoint, token, personal entity ID, address or operating frame is bundled.

## Verification and what it proves

The original captured modules and the port are run with the same inputs locally.
Network access is blocked in the comparison harness. Comparisons cover planner
outputs, controller actions and target settings, ceiling decisions, firmware
programs, reserve admission, recovery classification and ordered write failures.
The private capture and original deployment files are excluded from this repo.

Public regression tests use synthetic inputs. Home Assistant tests exercise the
real coordinator, setup form and storage helpers with synthetic states, including
reload, stale/missing data and rejected live-mode settings. They are software
tests; they do not establish electrical behaviour or firmware compatibility.

## Remaining work before live mode

1. Commission the register map, firmware, BMS frame, power signs and independent
   grid measurement on a separately authorized test installation.
2. Generalize the reference calibration and tariff assumptions into validated
   installation settings, and support daylight-saving delivery days.
3. Integrate supervised workers, daily program execution and owner notifications
   with the HA live lifecycle. Workers exist and run locally, but HA setup starts
   observation only. Whole-host failure requires a separately proven safeguard.
4. Test physical command effects, communication loss, reboot and outage behavior.
   A register readback does not prove that the battery has stopped charging.
5. Add live commissioning and activation only after those requirements pass.

Matching captured decisions proves policy agreement for the tested cases. It
does not yet establish a deployable replacement for the operating installation.
