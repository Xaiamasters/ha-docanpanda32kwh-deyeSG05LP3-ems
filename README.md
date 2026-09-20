# Docan Panda & Deye EMS

A Home Assistant energy controller for a **Docan Panda 32 kWh** battery and
**Deye SUN-10K-SG05LP3-EU-SM2** inverter. It connects to the equipment, creates
an energy dashboard, plans charging from electricity prices and can execute
that plan after you commission the installation and enable live control.

**0.5.0-beta.1 is an experimental control beta.** New installations start in
shadow mode. Software and simulated equipment tests do not establish physical
compatibility with every battery, logger or inverter firmware.

[![Hassfest](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hassfest.yaml)
[![HACS validation](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hacs.yaml/badge.svg)](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hacs.yaml)
[![Software tests](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/tests.yaml/badge.svg)](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/tests.yaml)

## What it does

| Feature | Behaviour |
|---|---|
| Direct equipment connection | Reads Deye over Solarman V5, Modbus TCP or RTU-over-TCP, and Docan through an independent USB/RS485 adapter |
| Automatic dashboard | Battery, power flow, solar, home and grid measurements, history, electricity prices, energy plan, optional sun map and operating controls |
| Price-based charging | Selects charging intervals from published prices, your battery state and the configured power and voltage limits |
| Export arbitrage | Selects evening exports when the actual selling price covers charging losses, wear and the required margin; export is disabled by default |
| Charge target | Uses a 90% target, or your lower maximum. It can select 95% when the extra energy fits, is profitable and has an export opportunity |
| Overnight reserve | Starts from your declared household-load estimate, then uses admitted overnight observations; incomplete measurements are rejected |
| Daily updates | Plans the next day at 23:15 and updates the current plan at 10:30 in HA's timezone; missed jobs and missing prices remain visible |
| Live control | Applies all six inverter time programs and ordered charge, hold, idle and export settings, with readback and a persistent stop latch |
| Independent guards | Separate processes monitor charging, export, controller health and unexplained setting changes |
| Forecast comparison | A separate shadow optimizer uses solar/load forecasts, optional price anticipation and efficiency learning |
| Recovery | Export/import your private settings, or restore a full HA backup. Restored installations require commissioning again |

The active controller uses **published prices**. Solar forecasts and anticipated
tomorrow prices belong to the separate forecast comparison and cannot authorize
equipment commands. The dashboard distinguishes estimates from recorded energy.

## What you need

The default equipment is a Docan Panda 32 kWh with the Deye 10 kW SG05LP3.
Bounded profiles also cover the **6, 8 and 12 kW SG05LP3** variants. Selecting a
profile does not certify its firmware. The command adapter caps export at 10 kW,
including on a 12 kW inverter.

1. A working **battery-to-inverter BMS communication cable** and equipment
   configuration appropriate to your exact revisions. Follow the manufacturer's
   port, pinout and protocol instructions. An RJ45 connector alone does not
   establish whether the protocol is CAN or RS485.
2. For live control, a **separate RS485-to-USB adapter from the Docan telemetry
   interface to the HA host is required**. This supplies independent battery
   readings. Enter its serial path and battery address. The supported profile
   has 16 cells, four probes, MOS and environment temperatures, at 9600 baud/8N1.
3. A compatible Deye network logger or gateway. Enter its local IP, port and
   Modbus unit. Solarman V5 also needs the logger serial number. No separate
   inverter integration is required for this direct connection.
4. **Tibber token and home**, or **Nord Pool area with your taxes and supplier
   fees**, or a compatible price sensor. Enter your export compensation
   separately. Nord Pool export can use spot minus your export fee. Tibber's
   import total is never assumed to be your selling rate.
5. Solar connection type and total installed **kWp**. Deye DC solar is read
   directly. External AC or mixed solar needs a combined production sensor from
   that equipment's integration.
6. Your limits: charging current, program power, voltages, charge/reserve floors,
   temperature limits, export permission/end time and estimated overnight load.
7. Optionally your home address label and selected coordinates for the sun map.
   No other household's address, location or connection settings are supplied.

Live commissioning requires healthy, fresh independent battery readings,
voltage-mode TOU, today's TOU weekday enabled, six valid program times and
inactive controls. Initial programming requires absolute grid power at most
500 W and absolute battery power at most 300 W. The integration does not switch
battery operating mode or configure BMS wiring automatically.

Existing HA sensors can still be used for observation and forecast comparisons.
They do not grant live control. See [equipment connections](EQUIPMENT_CONNECTION.md)
and the [control contract](CONTROL_ADAPTER.md).

## Install through HACS

Requires **Home Assistant 2026.6.1 or newer** and HACS. This is a custom
repository, not a HACS default-store entry.

1. Back up Home Assistant.
2. Open **HACS > menu > Custom repositories**.
3. Add `https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems`
   with category **Integration**.
4. Open **Docan Panda & Deye EMS > Download**. If needed, choose **Need a
   different version? > Release > 0.5.0-beta.1**.
5. Restart HA. Open **Settings > Devices & services > Add integration >
   Docan Panda & Deye EMS**.
6. Follow the setup wizard for hardware, connections, readings, location, solar,
   tariffs and planning. Select **Automatic charge and export engine** for live
   capability and review the equipment limits.
7. Open the new dashboard in the sidebar. It starts in **shadow** mode.

The dashboard cards, their licences and corresponding source are included.
HA installs the declared Python dependencies. A manual installation copies
`custom_components/docan_deye_ems` into `config/custom_components/` and continues
with the restart and integration setup above.

## Enable live control

Use the dashboard's **Operation** section:

1. Make the inverter available for this controller alone and bring it to the
   idle state required for programming. Competing automations, integrations or
   vendor controls must not keep writing to the same equipment.
2. Select **Check equipment**. Compare the displayed readings with the equipment
   displays and review the limits and commissioning confirmations.
3. Select **Confirm equipment and limits**. This starts the independent guards.
   Control is still inactive.
4. Select **Enable live control**. The integration builds a current plan,
   programs and verifies all six periods, then starts the controller.

The approval is bound to the equipment identity, firmware and installation
settings. Changes require a fresh check. **Stop control** revokes operation and
attempts the bounded stop settings. **Acknowledge reviewed stop** clears a
reviewed latch only after fresh idle checks; it leaves control inactive.

A restart or restored backup never resumes live operation automatically.
Reconfigure stops an active session before changing its connection or limits.
Failed stop readback prevents a normal unload from discarding the guards.

The stop sequence turns off grid charging and selling and selects zero export
to CT/load-first. It is not an electrical disconnect. Register readback is not
proof of stopped physical power flow. Guards on the same HA host cannot operate
through complete host power loss; the inverter and BMS must enforce their own
limits. See [commissioning and failure behaviour](CONTROL_ADAPTER.md).

## Sensors and reporting

Entity names use your installation name. Optional sources determine which
measurements exist.

| Group | Information |
|---|---|
| Battery | SoC, voltage, current, power, temperature and cell spread |
| Inverter and site | Inverter AC power, home load, grid and solar power; reported daily energy totals |
| Tariff and planning | Import price, structured energy plan, readiness and operating mode |
| Controller | Active/commissioned state, stop reason, verified program date and guard health |
| Forecast comparison | Forecast solar/load energy, planned import/export, costs, wear and projected SoC |
| Learning | Load model, measured efficiency where configured, forecast errors and observed daily grid statistics |

Battery power/current are positive when discharging. Grid power is positive
when importing. The direct grid reading is the inverter's measurement, not a
certified billing meter. Missing battery data never falls back silently to
inverter SoC. Required input loss during live operation stops control and raises
a local Repairs issue.

History builds on the new HA installation. A newly installed system has no
measured overnight history. It labels its configured estimate until enough
valid observations are available. See [engine behaviour](ENGINE_PORT.md) and
[forecast planning](PLANNING.md).

## Privacy and recovery

Enter your own connection details, location and provider credentials. The public
package contains no site credentials, private backup or learned household data.
Diagnostics omit credentials, location and connection identifiers.

The map and Forecast.Solar have separate opt-ins. Enabled map providers receive
selected coordinates and browser requests. Forecast.Solar receives coordinates,
orientation and kWp when selected and consented. Tibber receives its token only
for its price API.

**Export household settings** creates a private JSON file containing local
connection details, optional location, mappings, tariffs and limits. It excludes
the Tibber token, learned history and commissioning authority. Keep it private.
Re-enter credentials when restoring. A complete HA backup also preserves
history and durable controller state, but restoration still requires fresh
commissioning. Removing an entry removes its learning and planning stores;
controller stop/audit records are retained locally for review.

## Licence and validation

Original integration code uses **MIT**, with its terms in [LICENSE](LICENSE).
Bundled components keep their own licences: Helios is GPL-3.0-or-later; Sunsynk
power flow and Chart.js use MIT. Their source and notices are included. See
[third-party notices](THIRD_PARTY_NOTICES.md).

[Testing](TESTING.md) distinguishes software, disposable HA and physical
hardware evidence. For reproducible bug reports and private information to
exclude, see [support](SUPPORT.md).
