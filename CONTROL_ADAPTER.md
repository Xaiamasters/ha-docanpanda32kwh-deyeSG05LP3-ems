# Commissioning and equipment control

Version 0.5.0-beta.1 includes the HA control lifecycle and dashboard. Creating
or restoring an entry cannot activate equipment. Only an authenticated HA
administrator can confirm commissioning and enable live control.

## Approval and identity

The preview reads the inverter identity, rated power and firmware, and requires
three fresh independent BMS observations. The selected model must match rated
power. An expiring confirmation binds the device, endpoint, battery source,
tariffs, timezone and limits. The owner confirms equipment readings, BMS wiring,
sole-writer operation, limits, export permission and whole-host failure limits.

Confirmation starts four independent processes but leaves control inactive.
Enable live is a separate action. Every actual command reads and matches the
commissioned identity on the same connection before sending the write. Firmware
or device changes revoke the session. A settings change, restart or restore
requires fresh commissioning. An imported settings file cannot carry authority.

The adapter has named bounded fields, not a register editor or general command
service. The dashboard has authenticated admin-only operations for preview,
confirmation, enable, stop and acknowledgement. No HA control services are
registered. The loopback-only authority used by low-level tests cannot target a
physical host; installed control uses the separate commissioned authority.

## Register contract

These decimal addresses and encodings follow the LV branch of the upstream
[Solarman Deye three-phase definition](https://github.com/davidrapan/ha-solarman/blob/main/custom_components/solarman/inverter_definitions/deye_p3.yaml).
That definition is protocol evidence, not commissioning evidence for a particular
SG05LP3 firmware. No upstream implementation was copied into the command adapter.

| Field | Register | Encoding |
|---|---:|---|
| Battery operating mode | 111 | 0 voltage, 1 capacity, 2 absent; read only |
| Grid charge current / enable | 128 / 130 | A / off-on |
| Energy pattern | 141 | 0 battery first, 1 load first |
| Work mode | 142 | 0 export, 1 zero-export load, 2 zero-export CT |
| Export limit / solar sell | 143 / 145 | W / off-on |
| TOU | 146 | Enable bit 0; weekday bits preserved |
| Six program starts | 148-153 | HHMM |
| Six program power limits | 154-159 | W |
| Six program voltages | 160-165 | 0.01 V |
| Six program SoC limits | 166-171 | % |
| Six charging selectors | 172-177 | Disabled, grid, generator, both |
| Battery alarm | 220 | Nonzero is an alarm |
| Grid export limit | 231 | 10 W |
| Device alarms / faults | 553-558 | Any nonzero value rejects healthy status |

Read requests use function 03. Commissioned commands use function 16 with one register,
then an independent readback. The wire format follows the
[Modbus application specification, section 6.12](https://www.modbus.org/file/secure/modbusprotocolspecification.pdf).
Solarman envelopes follow the
[V5 protocol description](https://pysolarmanv5.readthedocs.io/en/latest/solarmanv5_protocol.html).
Actual firmware acceptance of these commands remains unverified.

## Transactions and stopping

Six-program application requires fresh idle equipment: absolute grid power
at most 500 W, absolute battery power at most 300 W, healthy alarms, grid charge
and solar sell off, zero export to CT and load-first. TOU is disabled while every
period is staged, then the complete block is verified before restoring its
previous enable bit. Weekday bits are preserved. Generator charging and
duplicate or unproven midnight boundaries are refused.

Commands record intent in SQLite with full synchronization before transmission.
Write echoes and independent readback are checked. Missing acknowledgement or
readback latches STOP without retrying the command. OS locks serialize device
roles, wire access and serial reads. A physical identity lock prevents two
commissioned entries on the same HA host owning the same inverter. This cannot
detect every writer on a different host; sole-writer confirmation remains a
commissioning requirement.

STOP attempts all four reductions: solar sell off, grid charge off, zero export
to CT and load-first. One failed reduction does not suppress the remaining
attempts. Failure to persist the stop is reported separately. Register readback
does not prove stopped physical energy flow and is not an electrical disconnect.

Acknowledgement requires the current latch ID and fresh healthy idle readings.
It clears only that reviewed stop and leaves the controller inactive. A policy
thread still running after cancellation blocks acknowledgement and retains
ownership until it exits. Normal unload is refused when stop readback remains
unverified, preserving the available guards for recovery.

## Independent guards

The charge and export guards read their own fresh equipment observations. The
supervisor checks controller and peer heartbeats. The auditor compares observed
settings with verified command outcomes, never with intentions or shadow
proposals. Unattributed changes stop control. The auditor cannot detect writing
the same value, or a setting changed and restored entirely between polls.

Guards run as separate Python processes with fixed roles and no command server.
They share durable stop state. Another process's STOP revokes queued commands
at the wire boundary. Lost guard heartbeats also prevent further activating
commands. Charge/export poll every 10 seconds, audit every 15 and supervision
every 5, with bounded transport operations and a 75-second heartbeat limit.

This separation covers a stalled or failed controller while its guard processes
and equipment connections remain alive. It does not cover complete HA
container/host power loss, dead networking, or unreachable equipment. Native
inverter/BMS protections must remain in force. Software tests use emulators;
actual acceptance by a particular firmware remains commissioning evidence
that cannot be inferred from passing simulation.
