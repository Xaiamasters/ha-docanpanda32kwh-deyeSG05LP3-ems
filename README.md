# Docan Panda & Deye EMS

**Development branch: 0.5.0.dev1.** This branch adds a production planning and
control-policy port for offline and Home Assistant shadow comparison. It is not
a published release and does not enable equipment control. See the
[engine port status and remaining work](ENGINE_PORT.md).

[![Hassfest](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hassfest.yaml/badge.svg)](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hassfest.yaml)
[![HACS validation](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hacs.yaml/badge.svg)](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/hacs.yaml)
[![Software tests](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/tests.yaml/badge.svg)](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/actions/workflows/tests.yaml)

A Home Assistant integration for the **Docan Panda 32 kWh** battery and
**Deye SUN-10K-SG05LP3-EU-SM2** inverter. It reads equipment data, adds an energy
dashboard and calculates charging and export plans from prices and forecasts.
Profiles for the 6, 8 and 12 kW SG05LP3 variants are also included.

**Current release: 0.4.0-beta.1. It reads and simulates only. It cannot control
the inverter or battery, and it has no live-mode setting.** Software tests have
passed, including installation through HACS. Physical hardware compatibility
has not been verified.

## Project direction

The intended finished product is a deployable EMS with three operating modes:

| Mode | Intended behaviour | Available in 0.4.0-beta.1? |
|---|---|---|
| Read-only | Show equipment readings and energy use | Yes |
| Shadow | Calculate a plan without sending equipment commands | Yes |
| Live | Execute the plan after the owner completes setup checks and explicitly enables control | No, requires a controller implementation |

New installations must start without equipment control. The live controller,
its activation checks and its handling of failures still need to be built and
validated. The current beta cannot replace an operating EMS controller.

## Available features

| Feature | Current behaviour |
|---|---|
| Equipment connection | Built-in Deye telemetry over Solarman V5, Modbus TCP or RTU-over-TCP; optional independent Docan USB/RS485 readings |
| Energy dashboard | Battery, power flow, optional sun/location map, solar, home, grid, price chart, simulated schedule and history |
| Solar forecast | Built-in Forecast.Solar for one panel orientation, or an aggregated forecast sensor for multiple orientations |
| Home consumption forecast | Uses an entered average initially, then learns a daily pattern from recorded observations |
| Import/export optimization | Simulates charging, self-consumption and optional battery export using solar, load, tariffs, losses, reserve, power limits and battery wear cost |
| Price anticipation | Optional historical estimate of unpublished tomorrow prices, clearly labelled and drawn with a dashed line |
| Efficiency learning | Optional measured charge/discharge efficiency when a dedicated battery AC power measurement is available alongside DC power |
| Cost reporting | Measured grid costs, data coverage and forecast errors; simulated savings are not claimed as achieved savings |
| Recovery | Private settings export/import and stable installation identity; normal HA backups preserve the full configuration |

See [planning](PLANNING.md) for the calculations and limitations, and
[testing](TESTING.md) for the checks completed on this beta.

## Hardware and information needed

1. A commissioned Docan Panda 32 kWh battery and the selected Deye inverter, with
   their **working battery-to-inverter BMS communication cable** in place. The
   correct BMS port/protocol depends on the manufacturer's instructions for the
   exact revision. A connector shape alone does not identify CAN versus RS485.
2. For **independent Docan readings**, an **RS485-to-USB cable/adapter between the
   battery's telemetry interface and the HA host is required**. This is a separate
   connection from the battery-to-inverter BMS cable. Enter the USB serial path
   and configured battery address. The supported reader uses 9600 baud, 8N1 and
   one fixed telemetry query. Alternatively choose inverter/existing readings.
3. A compatible Deye network logger/gateway: local IP, port and Modbus unit, plus
   logger serial for Solarman V5. A separate equipment integration is not needed
   for the supported direct setup. Existing HA sensor mappings remain available.
4. Your solar connection type and total installed **kWp**. Deye DC MPPTs are read
   automatically. External AC/mixed solar needs a combined production sensor
   supplied by that equipment's integration.
5. A price source: **Tibber token and home**, **Nord Pool area plus household taxes
   and fees**, or a compatible price sensor. Export compensation is entered
   separately; import price is never treated as export revenue.
6. For forecasting: panel tilt/azimuth and consent to send coordinates to
   Forecast.Solar, or a suitable forecast sensor; average home load for cold start;
   reserve, targets, limits, efficiency assumptions and wear cost.
7. Optionally, your home address label and selected coordinates for the sun map.
   No address or coordinates from another household are included.

See [equipment connections](EQUIPMENT_CONNECTION.md) for the supported interfaces.

## Installation

Install the beta through a **HACS custom repository**. It is not in the HACS
default store. The declared minimum Home Assistant version is 2026.5.3.
Testing used 2026.6.1.

1. Back up Home Assistant and configure HACS.
2. Open **HACS → menu → Custom repositories**.
3. Add `https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems` with
   category **Integration**.
4. Open **Docan Panda & Deye EMS → Download → Need a different version?**.
   Select **Release → 0.4.0-beta.1**, then **Download**. HACS 2.0.5 may initially
   offer a commit identifier; select the named prerelease explicitly.
5. Restart HA. Open **Settings → Devices & services → Add integration →
   Docan Panda & Deye EMS**.
6. Complete the wizard: hardware → battery source/readings check → inverter
   connection/readings check → optional home location → solar → import tariff →
   export tariff → planning mode → forecast and optimization assumptions.
7. Open the automatically added dashboard in the sidebar.

No separate dashboard cards need installing. Their licensed runtime assets,
notices and source are bundled. HA installs the declared Python dependencies.
For manual installation, copy only `custom_components/docan_deye_ems` into
`config/custom_components/`, restart HA, and continue at step 5.

Use **Reconfigure** to change configuration. Do not run a second poller against an
exclusive USB device or a single-client gateway; use existing HA sensors instead.

## Sensors and entities

Available measurements depend on the selected connection and optional mappings.
Names are prefixed with your installation name; identifiers are assigned by HA.

| Group | Entities / information |
|---|---|
| Battery | SoC, voltage, current, power; independent Docan mode also gives temperature and cell-voltage spread |
| Inverter and site | Inverter AC power, home load, grid power, solar power; reported daily solar/load/import/export totals where supported |
| Tariff and plan | Current import price, structured observed or shadow plan, readiness binary sensor |
| Forecast plan | Solar/load forecast energy, planned grid import/export, net grid cost, wear cost and projected end SoC |
| Learning and reporting | Active load model, charge/discharge efficiency used, solar/load mean absolute error, shadow/observed cost difference and observed daily statistics |

Battery power/current are positive when discharging; grid power is positive when
importing. Unknown or stale measurements remain unavailable. A missing required
input hides the plan and raises a Repairs warning. A failed independent battery
read never silently substitutes inverter-reported SoC.

Historical charts and learning need observations from this Home Assistant
installation. A new installation has no previous history. Forecast totals cover
the remaining planning period, not an entire day's meter readings.

## Privacy and backups

Each owner enters their own equipment connection, location and tariff settings.
The release excludes household credentials, backups and learned history.

The map and solar forecast have separate opt-ins. Map providers receive selected
coordinates/browser requests when enabled; Forecast.Solar receives coordinates,
orientation and kWp when selected and consented. Turning off the map does not
turn off a separately enabled solar forecast. Tibber receives its token only for
its price API. Diagnostics omit credentials, location and connection identifiers.

**Export household settings** produces a private JSON file containing local
connection details, optional location, mappings and tariff. It excludes the
Tibber token and learned history. Keep this file private and re-enter the token
when restoring. A complete HA backup is needed to preserve credentials, learned
history and recorder data. Removing the integration deletes its learning store;
HA's recorder retains history according to its own retention policy.

## Licence

The original integration code currently uses **MIT**. People may use, change,
redistribute or sell that code, including in closed-source products, provided
they retain the required copyright and licence notices. It is supplied without
a warranty. See [LICENSE](LICENSE) for the terms.

Bundled dependencies keep their own licences. **Helios uses GPL-3.0-or-later**;
the Sunsynk power-flow card and Chart.js use MIT. The top-level MIT licence does
not replace their terms. Their source and notices are included. See
[third-party notices](THIRD_PARTY_NOTICES.md).

For bug reports, see [support](SUPPORT.md).
