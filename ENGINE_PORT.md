# Automatic charge and export engine

The active policy is ported from an operating installation into modules with
explicit clocks, observations, transport and storage. It contains no connection
to that installation. New household configuration supplies every endpoint,
tariff, location and limit.

## Planning and execution

- Use published quarter-hour import costs and net export compensation as
  separate curves. Taxes and supplier fees affect import. Export has its own
  fixed, explicit sensor curve or Nord Pool spot-minus-fee basis.
- Select charging before the 18:00 local deadline using the reference tapered
  charge curve, clipped by the selected model, current and program-power limits.
  The curve is an assumption for a new household, not measured acceptance of its
  pack. First-day and morning plans use remaining intervals only.
- Use the configured overnight reserve, then measured admitted overnight
  draw. Cold start is labelled as a household-load estimate, with zero measured
  nights. Missing samples, outliers and excessive reserve movement are checked.
- Select profitable evening exports, bounded by energy above reserve,
  installation permission, power and the configured end time. Charge target is
  normally 90%, or a lower configured maximum. Conditional 95% requires a
  reachable target, positive marginal value and remaining export opportunity.
- Build tomorrow's plan at 23:15 and update today's at 10:30, using HA's
  timezone. Each has a five-minute delivery window. Missing tomorrow prices are
  retried within that window. A missed job is recorded. A late new installation
  builds a remaining-day plan rather than silently replaying a missed job.
- Convert charging to six firmware periods. Apply them only while observations
  and controls satisfy the idle programming checks. No export is delegated to
  an unverified native TOU export recipe.
- During live operation, repeatedly calculate CHARGE, HOLD, IDLE or EXPORT from
  the actual firmware window, state of charge, pinned reserve/ceiling, alarms,
  temperatures and fresh observations. HOLD retains the charging voltage inside
  the firmware window while grid charging is disabled.

The controller keeps durable intent, verified outcomes and stop state. Commands
are not repeated after an uncertain response. A stop requires manual review and
fresh idle proof; acknowledgement does not re-enable control.

## Calendar handling

Price intervals retain real timezone-aware instants. Short and long delivery
days use their actual number of quarter hours. No missing interval is invented.
The inverter's firmware periods use wall-clock times, so charging is excluded
from the repeated or skipped clock-change range. On a European fall-back day,
the first eligible charging boundary is 03:00. Export instants and energy retain
the correct delivery-day indexing. Unproven midnight program starts are refused.

## Separate forecast comparison

The forecast optimizer remains available in shadow mode. It uses solar/load
forecasts, can estimate unpublished prices, and can learn efficiency from
dedicated AC/DC measurements. Those estimates do not authorize the live policy.
See [forecast planning](PLANNING.md).

## Observations and supplied frames

Live control requires direct Deye and independent Docan reads. The BMS supplies
16 cells, four probes, environment and MOS temperatures. Pack/cell voltage
coherence and three consecutive valid reads are required. Missing input resets
admission. Deye voltage mode and today's TOU weekday must already be enabled.

For comparison only, an existing HA sensor can supply `schema: 1`, `updated_at`,
`snapshot`, optional `day_plan` and `overnight_draws`. The snapshot contains
`soc`, `pack_v`, `p1_w`, control flags/settings, all six `programs`, `temps`,
source `ages`, alarms, `docan_fresh`, `truth_ready` and owner-stop flags. Missing
values must remain missing. Supplied observations never create write authority.

## Evidence boundary

The initial extraction was compared offline with saved source modules across
planner, controller, ceiling, schedule, reserve and failure cases. This public
version deliberately extends that policy with separate tariffs, household
limits, actual delivery intervals and explicit first-install assumptions.
These extensions are covered by their own synthetic tests. They are not a claim
of bit-for-bit equivalence to a private installation or physical certification.
