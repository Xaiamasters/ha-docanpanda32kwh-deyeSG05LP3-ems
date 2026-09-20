# Built-in equipment connection

Docan Panda & Deye EMS creates the equipment sensors during setup. For the default
Docan Panda 32 kWh + Deye SUN-10K-SG05LP3-EU-SM2 DC-solar installation, the person
installing it supplies the local adapter details instead of selecting sensors.
All 6/8/10/12 kW family profiles remain field-verification candidates.

## Connection choices

| Choice | Required information | Already installed outside Docan Panda & Deye EMS |
|---|---|---|
| Solarman V5 | Local IP, TCP port (usually 8899), logger serial, unit address | A compatible V5 logger connected to the inverter and local network |
| Modbus TCP | Local IP, configured TCP port (usually 502), unit address | An RS485 gateway configured to translate Modbus TCP to the inverter's RTU bus |
| Modbus RTU over TCP | Local IP, configured TCP port, unit address | A transparent serial network gateway passing raw RTU frames |
| Existing HA sensors | Select local measurements and their directions | An equipment integration already providing those measurements |

The logger serial is different from the inverter serial. Ports alone do not
identify protocols. Deye-branded loggers that do not speak Solarman V5 are not
implicitly supported. Direct USB-to-Deye adapters and cloud-only loggers are outside this
build's Deye connection support. The independent Docan reader uses USB/RS485
separately, as described below. No external HA equipment integration is
required for the three supported network protocols.

The battery BMS CAN/RS485 link belongs between battery and inverter. It is not
the HA telemetry connection. The integration does not configure a gateway's
baud rate, parity, wiring, BMS protocol, CT direction or inverter settings. Use
the manufacturer's manual for the exact hardware revision; do not repurpose
the battery BMS port. Existing pollers should use the existing-sensor path to
avoid competing requests on a single-client gateway or serial bus.

## Independent Docan battery telemetry

This mode requires an RS485-to-USB adapter connected from the battery's supported
telemetry interface to the HA host. Select its stable `/dev/serial/by-id/` path
when available and the configured battery address (0 to 15). The reader uses 9600
baud, 8 data bits, no parity, one stop bit and a bounded two-second response wait.
It does not scan addresses or change the battery's configuration.

The implemented profile sends one fixed Pylon-style ASCII telemetry query and
accepts the bounded 16-cell VER22/CID1 4A response layout. Framing, address, length,
checksum and plausible measurement ranges must all match. The current field is
separate from the following resistance field; charging current is normalized to
negative values. SoC, pack voltage/current/power, temperature and cell-voltage
spread are exposed. Raw frames, device identity and battery alarms are not exposed.
This decoder is not a battery protection or alarm system. Other BMS layouts are
not accepted; actual Docan firmware compatibility still needs field evidence.

The same serial port must not be owned by another integration/process. The
adapter must be exposed to HA Core by the host. A USB device path is private
configuration and is excluded from diagnostics. If the independent reader fails,
the integration marks battery values unavailable and hides the plan; it never
silently falls back to Deye-reported battery values.

The battery-to-inverter BMS cable remains a separate required equipment link.
Docan's published Panda compatibility table lists Deye under CAN, while the
inverter exposes CAN/RS485 BMS options. Confirm the actual battery/inverter
revision and pinout rather than assuming that an RJ45-style cable proves RS485.
See the [Docan Panda manual](https://www.docanpower.com/index.php?download_id=106&product_id=651&route=product/product/download).

## Setup and resulting entities

The wizard tests the selected endpoint, checks that register 0 identifies a
low-voltage three-phase hybrid (5 or 1280), and checks plausible battery voltage
and state of charge. It shows battery, inverter, load, grid and DC solar readings
for comparison with the inverter screen before continuing. This identifies a
device class; it does not prove the exact model, battery brand or firmware.

Direct mode automatically supplies SoC, battery voltage/current/power, inverter
AC power, grid import/export, reported load, DC solar (MPPT1 + MPPT2), and daily
solar/load/import/export totals. Home/grid coverage depends on the inverter's
correctly commissioned meter/CTs. A separate external AC or mixed solar source
still needs an explicit combined-production sensor; Deye-only daily solar totals
are hidden in those modes to avoid presenting them as complete totals.

Sensors have stable installation identities. Reconfiguring an existing beta
entry can switch from sensor selection to direct reads without changing those
identities. Settings restore always returns to the wizard and tests the endpoint
again. A full HA backup preserves the configured connection.

## Read-only boundary and failures

Only Modbus function 03 (read holding registers) can be constructed. Requests use
a fixed telemetry allowlist: (0,1), (587,5), (625,1), (636,1), (653,1), (672,2),
(520,2), (526,1), (529,1), expressed as (start,count). There is no generic request
API, arbitrary register input, plant service, control entity, write function or
discovery scan. Each polling cycle opens one connection, performs bounded reads,
then closes it. Polling is every 30 seconds, with a 15-second total deadline and
3-second individual response deadline. The serial unit cannot be broadcast zero.

Transaction identity, lengths, function, unit, Solarman serial/sequence/status,
outer checksum and RTU CRC are checked as applicable. Failed cycles discard the
whole equipment snapshot and hide plan windows; they never retain an old reading
as live or substitute zero. The next successful cycle recovers automatically.
Unsupported daily-counter sentinel values remain unavailable.

This initial LV profile uses published 16-bit power registers: signed grid,
inverter and battery power, unsigned reported load and MPPT power. Signed power
range is -32768 to 32767 W. Sites requiring wider grid measurements, different
register layouts or high-voltage profiles need a verified additional profile;
they must use the existing-sensor path until then. Hardware/firmware field tests
remain a release gate; simulated protocol tests are not physical certification.

Local IP and logger serial are private HA configuration. They are excluded from
diagnostics and dashboard snapshots. The explicitly private settings export
includes them for recovery; it excludes Tibber credentials. The integration code
contains no household endpoint or credential defaults.

## Protocol references

This reader is implemented within Docan Panda & Deye EMS using Python's standard library; no
third-party control integration is installed or copied. Register facts were
cross-checked against the upstream Solarman definitions, and V5 framing against
the protocol documentation. Their authors retain their own code/licenses.

- [Deye SG05LP3 family manual](https://www.deyeinverter.com/deyeinverter/2026/03/19/BManualSUN-3-12K-SG05LP3-EU-SM220260319en.pdf)
- [Solarman V5 wire protocol](https://pysolarmanv5.readthedocs.io/en/latest/solarmanv5_protocol.html)
- [Legacy low-voltage three-phase telemetry map](https://github.com/StephanJoubert/home_assistant_solarman/blob/main/custom_components/solarman/inverter_definitions/deye_sg04lp3.yaml)
- [Current Deye three-phase device classes and register map](https://github.com/davidrapan/ha-solarman/blob/main/custom_components/solarman/inverter_definitions/deye_p3.yaml)
