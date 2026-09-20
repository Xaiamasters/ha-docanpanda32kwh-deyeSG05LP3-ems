# Production engine port

This is the **unreleased 0.5.0.dev1 development candidate**. It adds the planning
and controller policy extracted from an operating installation, with household
identifiers and external access removed. The running installation was read only.
This candidate has not been installed there or published as a HACS release.

The port is available for offline comparison and as a Home Assistant shadow
policy. **It cannot send equipment commands.** The transaction code is exercised
against an in-memory device. A physical equipment adapter is still required
before this can become a live EMS.

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
| Deadmen and recovery | Includes pure stop-decision and recovery-classification functions; independent running watchdogs and notifications are not connected |
| Home Assistant | Adds a selectable production-policy shadow adapter with local state persistence, input errors and plan details |

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

Select **Production policy comparison** during plan setup and supply a local
sensor carrying this observation contract in its attributes:

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

The current built-in telemetry reader does not produce this complete controller
frame. This adapter is a comparison interface, not the finished all-in-one
equipment connection. It stores up to 14 days of observations privately under
this installation's HA storage key. Rebinding the source or changing comparison
settings invalidates prior engine state. No endpoint, token, personal entity ID,
address, actual price history or captured operating frame is bundled.

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

1. Build the complete equipment observation adapter, including independent BMS
   truth/freshness, six temperatures, alarms and all six firmware programs.
2. Generalize the reference calibration and tariff assumptions into validated
   installation settings, and support daylight-saving delivery days.
3. Add a physical writer with verified model/firmware mappings, exclusive writer
   ownership, durable intent records and commissioning checks. Do not infer
   register writes from the read-only register decoder.
4. Connect daily program application, independent charge/export watchdogs, the
   write auditor, startup supervision and owner notifications. Recovery must
   remain observe-only unless explicitly commissioned; the captured production
   recovery job does not automatically clear its STOP latch.
5. Test power loss, HA restart, stale or unavailable sensors, competing writes,
   interrupted transactions, network loss and STOP recovery in an isolated lab.
   Then prove device behaviour on a separately authorized test installation.
6. Add the owner's live-mode commissioning flow only after those checks pass.

Matching the captured decision proves a policy comparison at that moment. It
does not prove a full replacement for the original operating installation.
