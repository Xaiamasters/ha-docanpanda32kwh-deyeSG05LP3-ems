# Beta support

This experimental beta includes observations, shadow planning and explicitly
commissioned live control. The 6/8/10/12 kW profiles have software test coverage;
physical compatibility must be established for the exact equipment and firmware.
There is no promised response time or SLA. Review a stopped controller locally
before acknowledging it, and retain the equipment's native protections.

Report reproducible software bugs through
[GitHub Issues](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/issues).
Include the integration and Home Assistant versions, selected transport,
inverter model/firmware, reproduction steps, and expected versus actual behavior.
Use integration diagnostics where possible and review every attachment yourself.

Never attach HA backups, household-settings exports, access tokens, passwords,
private keys, full configuration stores or unredacted logs. Remove addresses,
coordinates, public/private IPs, logger serials and household identifiers from
screenshots and examples. Settings exports omit the Tibber token but retain
other personal configuration and must stay private.

For a security issue, use the repository's private vulnerability-reporting
feature when enabled. Do not post working credentials in a public issue.

An unavailable reading is not permission to change inverter settings, bypass
safety protections, or run multiple controlling systems. Check the local
integration setup and consult the relevant equipment documentation.
