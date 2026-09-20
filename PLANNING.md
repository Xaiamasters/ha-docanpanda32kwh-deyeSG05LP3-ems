# Forecasts and read-only planning

The default forecast mode simulates a feasible energy schedule. It cannot execute
that schedule. The other modes display a structured external controller plan or
calculate the simpler price-only charging estimate.

## Price horizon and tariffs

The horizon is the remainder of today, plus tomorrow when complete published
prices are available. Delivery days use the HA timezone and validate 23/24/25-hour
DST days. Tibber totals are final import prices. For Nord Pool raw spot:

`final import = (spot + energy tax + supplier fee) * (1 + VAT / 100)`

Amounts are per kWh in the selected currency; taxes/fees are entered before VAT.
Already-final prices are not taxed twice. Fixed monthly fees are excluded.

Export compensation is either your entered fixed net price or an explicit export
curve from a price sensor, less the entered export fee. The fixed net price is
already final. No import/export price equivalence is assumed.

Optional anticipation fills unpublished tomorrow prices only after at least three
distinct historical days exist for every local quarter. It uses up to 14 days of
same-time historical prices, with a mean and standard-deviation spread. This is
a simple statistical estimate, not a weather/market model or calibrated confidence
interval. Optimization uses the adverse side of that spread for anticipated
prices. The chart and plan mark these intervals. Published prices replace the
estimates when available. Without enough history, the horizon stays at the end
of the published day. Battery export on anticipated prices is separately disabled
by default.

## Solar and home demand

The built-in Forecast.Solar option supports **one panel orientation**. Enter tilt
and azimuth (south 0°, east -90°, west 90°), kWp and coordinates. One request is
attempted per hour. TLS is verified, redirects are disabled, and transport errors
omit coordinate-bearing URLs. A failed refresh can use the prior result for up to two hours,
provided it still covers the horizon; beyond that the plan is unavailable.
Daylight power is integrated into interval energy; unsupported gaps are rejected.
This is an estimate, not a guarantee of production or a detailed shading model.

For several roof orientations, select an aggregated external forecast sensor.
Its available state must carry these attributes:

```yaml
updated_at: "YYYY-MM-DDTHH:MM:SS+02:00"
unit_of_measurement: "kWh"
periods:
  - start: "YYYY-MM-DDT12:00:00+02:00"
    end: "YYYY-MM-DDT12:15:00+02:00"
    kwh: 0.25
  # Contiguous intervals covering the whole remaining price horizon, including night.
```

Each interval is at most one hour, with no gaps/overlap, at most 300 intervals,
and an update within two hours. A no-solar installation explicitly uses zero PV.

Home demand starts with your entered average load. A local quarter-hour profile
replaces that baseline where at least three different prior days each have at
least 850 seconds of observations in that quarter. The profile uses a median;
after eight sufficiently covered recent quarters it applies a bounded intraday
adjustment of 0.5–1.5. Gaps and downtime are not interpolated. There is no occupancy,
EV-arrival or appliance schedule model in this beta.

## Optimization assumptions

The planner uses a finite-state energy model with up to 200 intervals, at most
15 minutes each, and a 0.25 kWh battery lattice for the 32 kWh pack. Exact initial,
reserve, target and terminal values are included. Results are optimal on that
discretized model, not a continuous physical optimum; low-power operation can
alternate between intervals because of the lattice.

It accounts for forecast PV/home energy, import/export tariffs, separately entered
charge/discharge efficiency, wear cost per DC kWh discharged, AC charge/discharge
limits, whole-home grid limits, reserve and maximum SoC, today's target/deadline,
and minimum end-of-horizon SoC. Initial limits are assumptions that the installer
must set to match the site; they are never written to equipment.

There is no simultaneous battery charge/discharge or grid import/export. Export
simulation requires an explicit export tariff and battery-export opt-in. There
is no assumed PV curtailment: impossible export/power/SoC limits produce no feasible
plan. A target before the active deadline must be reachable. Once today's
deadline has passed, the dashboard says so and the terminal reserve remains in
force; the deadline is not silently moved to tomorrow.

This aggregate model does not model per-phase constraints, battery temperature
derating, inverter clipping, detailed DC/AC solar conversion or BMS protection.
The integration does not change any device to make its assumptions true.

## Learning and cost reports

Efficiency learning requires **dedicated battery AC branch power**, positive for
discharge, alongside DC battery power. Grid power and total inverter power are
not suitable substitutes. Direction changes, values below 100 W and implausible
ratios are rejected. Each direction needs at least 60 samples and 0.5 kWh input
before its measured value replaces the entered assumption.

Observation integration accepts continuous gaps of at most 90 seconds. The local
store retains up to 30 days of quarter-hour observations, prices and prediction
records; cumulative efficiency aggregates persist until a relevant settings
change or removal. Equipment, source, direction, tariff or forecast changes reset
learning. No energy is integrated across an HA restart or missing-input gap.

Solar/load errors compare previously stored forecasts with sufficiently covered,
completed intervals. The daily cost table reports whole-home import minus export
compensation with observed hours. It is an estimate from telemetry, not an invoice.
An unpriced export is flagged rather than silently treated as free.

The mean absolute **shadow-versus-observed cost difference** compares an
unexecuted simulated plan with what actually happened. It is not cost-forecast
accuracy, realized arbitrage profit, or proof that this integration saved money.
Battery attribution, counterfactual savings and settled supplier accounting are
not implemented.

## Optional local price and observed-plan inputs

A price sensor must be available and contain a complete current delivery day;
complete tomorrow intervals may follow:

```yaml
date: "YYYY-MM-DD"
unit_of_measurement: "EUR/kWh"
periods:
  - start: "YYYY-MM-DDT00:00:00+02:00"
    end: "YYYY-MM-DDT00:15:00+02:00"
    value: 0.25
    export: 0.05
```

Other selected currencies and per-MWh units are supported. For curve export,
every used interval needs its separate export compensation.

An observed controller-plan sensor carries `date`, a fresh `updated_at`, and
non-overlapping `windows` with `start`, `end`, and `action` (`charge`, `export`,
or `idle`). Its state is the displayed status. Observing it never authorizes this
integration to execute it.
