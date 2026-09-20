# Sensors

All entities belong to the installation's device in **Settings > Devices &
services > Docan Panda & Deye EMS**. Home Assistant constructs entity IDs using
your installation name. Renaming an entity does not change its unique ID.
Existing sensor IDs are retained when upgrading.

The reporting sensors exist in automatic, shadow and supplied-plan modes.
Unavailable means the required input or history is missing. It does not mean
zero. They are observations and calculations; changing a sensor cannot enable
equipment control.

## Controller and plans

| Sensor name | Unit | Meaning |
|---|---|---|
| Controller state | none | Actual controller action when live; otherwise the operating mode |
| Operating mode | none | Shadow, live, stopped or read-only; includes commissioning and guard health |
| Controller power setpoint equivalent | W | Positive export limit or negative charging power ceiling; see the distinction below |
| Controller charge current setpoint | A | Verified forced grid-charge current; zero when no forced charging is requested |
| Energy plan | none | Current status with the active schedule and decision attributes |
| Planned grid charge hours | h | Remaining time in the active plan's grid-charge windows |
| Comparison plan | h | Remaining grid-charge time in the independent DP plan; full schedule in `horizon` |
| Observations ready | binary | Required observations and active-plan inputs are available |

Deye charging uses a current setting and voltage/program limits. It does not
use the neighbour's single signed-watt command. During charging the watt sensor
is `-min(current * target voltage, program power limit)`, an **equivalent
ceiling**, not measured power or a command to deliver that exact wattage.
During export it is the verified export limit. The separate current sensor
preserves the actual A setting. The `basis` and `verified_at` attributes explain
which applies. Both setpoint sensors become unavailable on STOP, in shadow,
after failed convergence or after 90 seconds without fresh verification.
Zero means no forced grid transfer; solar charging and self-consumption may
still produce nonzero battery power.

The comparison plan never changes the automatic controller's schedule, limits,
guards or command authority. It refreshes approximately every 15 minutes in a
background task. The controller continues on its own polling cycle.

## Forecasts and results

| Sensor name | Unit | Meaning |
|---|---|---|
| Solar forecast horizon | kWh | Forecast solar energy across the comparison horizon |
| Load forecast horizon | kWh | Forecast home consumption across that horizon |
| Planned grid import / Planned grid export | kWh | Simulated whole-home grid energy |
| Planned net grid cost | configured currency | Simulated import cost minus export revenue |
| Planned battery wear cost | configured currency | Entered wear rate applied to simulated battery discharge |
| Projected end charge | % | Simulated battery state at the end of the horizon |
| Charging efficiency used / Discharging efficiency used | % | Assumptions used by the comparison model; dedicated AC/DC measurements can support learning when configured |
| Active load model | none | Entered baseline, learned profile or a mixture |
| Solar battery charge estimate today | kWh | Solar-first attribution of observed charging, with coverage in seconds |
| Observed daily grid statistics | days | Number of observed days; `days` contains import, export, net cost and coverage |

Solar attribution is an estimate, not a dedicated energy meter. For AC solar,
home consumption is subtracted first and the surplus is converted using the
charging efficiency. For Deye DC solar, home demand is converted to DC using
the discharge efficiency first. Attributed energy never exceeds observed DC
battery charging. A mixed AC/DC total without a split cannot be attributed and
leaves this estimate unavailable. Its coverage can be partial and resets by
local calendar date. Daily grid costs cover the whole home, not battery profit.

The automatic engine can use an optional solar forecast for comparisons.
Select **Configure an optional solar forecast for comparison sensors** during
the equipment-limits setup step. External location access still requires
explicit consent. With no configured provider, comparison reporting learns a
same-quarter solar profile from at least three complete prior days. It does
not invent sunshine while that history is missing. No-solar installations can
start the comparison immediately. A local historical solar profile does not
anticipate tomorrow's weather.

## Accuracy and hindsight

| Sensor name | Unit | Meaning |
|---|---|---|
| Load forecast mean absolute error | W | Average absolute difference between issued load forecasts and observations |
| Solar forecast mean absolute error | W | Equivalent comparison for solar on the model's AC basis |
| Shadow versus observed cost difference | configured currency | Mean absolute quarter-hour cost difference; this is not hindsight regret |
| 12 hour load forecast energy error | kWh | Mean absolute total-energy error of completed 12-hour forecasts |
| 24 hour load forecast energy error | kWh | Mean absolute total-energy error of completed 24-hour forecasts |
| Load forecast P50 pinball loss | W | Quantile loss for the median load prediction |
| Load forecast P80 pinball loss | W | Quantile loss for the upper load prediction |
| Daily hindsight regret | configured currency | Yesterday's observed cost including estimated wear minus a hindsight optimum |
| Grid over buy versus hindsight | kWh | Positive excess of observed whole-home imports over the oracle's imports |
| Grid under buy versus hindsight | kWh | Positive shortfall of observed imports against the oracle's imports |
| Seven day DP versus controller schedule cost delta | configured currency | Sum of paired completed-day replay costs: DP minus price-window schedule; negative favours DP |

Forecasts are saved before their delivery intervals and never rewritten using
later observations. One 12-hour and one 24-hour vintage are retained per hour,
up to 30 days. They are elapsed-hour horizons, including clock changes, and
require every quarter of the horizon to be observed. Sample counts are exposed
in attributes. The P50 forecast begins with the entered baseline; learned P50
and P80 use per-quarter historical samples. P80 stays unavailable until at
least three distinct prior days support the corresponding quarter. These are
empirical profiles, not a claim of a trained machine-learning model.

Daily hindsight scoring requires a fully observed, priced local day with SoC
inside the comparison model's bounds. Days with missing samples, unknown export
prices, unseparated mixed PV or a tariff change within a quarter are not scored.
Both 23-hour and 25-hour days are supported. No integration occurs across gaps
longer than 90 seconds or over an HA restart.

The hindsight oracle uses realized load, solar and prices with the same
capacity, power limits and efficiency assumptions. It starts at the observed
initial SoC and must finish at least as full as the observed final SoC. It
optimizes on a finite SoC lattice, including wear. Regret is a signed model
comparison and can be slightly negative because measured equipment does not
exactly match that model or lattice. It is not an invoice or proven savings.
Over/under buying compares total import volume with that oracle; it does not
establish that energy was physically wasted or that demand went unmet.

For the seven-day comparison, both candidate requests must have been saved
before delivery. They are replayed against the same realized load/PV, initial
SoC, physical model and limits. A common terminal export value accounts for
different ending energy. Today's controller windows are used when available;
future published days use the same pure price-window planner with the entered
reserve and current SoC. This is a comparison of schedules, not a replay of
every live BMS guard, charge taper or firmware decision. `paired_days` shows
how many of the last seven completed days qualified. Missing days are omitted,
not treated as zero. Scoring never provides a route to the equipment.

Load errors can appear after the first observed delivery intervals. Horizon
errors need 12/24 hours after an issued forecast. Daily regret needs a complete
day, and the paired comparison additionally needs both issued plans. A fresh
installation will therefore show some unavailable sensors while it collects
evidence. Changing equipment, tariffs, planning limits, forecast settings or
time zone resets the learning context so incompatible histories are not mixed.
