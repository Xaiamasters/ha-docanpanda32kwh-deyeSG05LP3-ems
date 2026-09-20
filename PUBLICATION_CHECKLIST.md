# Public-beta publication checklist

Target repository: `Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems`.
Version: `0.4.0-beta.1`. The owner approved this separate public beta on
2026-09-20. Default-store submission and a brands PR remain separate decisions.

## Prepare the candidate

- [x] Finalize metadata, HACS instructions, beta scope, support and source notices.
- [x] Pass official hassfest, local upstream HACS schemas, software tests and
  dependency/read-only checks on the exact final files.
- [x] Review the recursive secret/owner-data scan, including bundled source
  archives. Only the approved public maintainer identity belongs in metadata.
- [x] Seal the public repository and release ZIP with SHA-256 sums. Exclude
  private evidence, deployment tools, backups and household settings.
- [x] Keep the existing private repository and its entire history private.
  No Git history is copied into this staged candidate.

## Authorized publication checks

- [ ] Start this separate public edition with clean Git history and a GitHub
  noreply commit email. Never push the private repository's history.
- [ ] Create this separate repository with Issues enabled, an accurate read-only
  beta description and topics such as `home-assistant`, `hacs`, `deye`, `docan`,
  `energy-monitoring` and `solar`.
- [ ] Push only the sealed public tree. Verify the remote commit and inspect
  hassfest, software-tests and HACS workflow outcomes. Fix actual failures;
  do not ignore checks or show passing badges for checks that did not run.
- [ ] Publish GitHub prerelease `0.4.0-beta.1` with its matching source/release ZIP,
  checksum, changelog and experimental hardware limitations.
- [ ] In a disposable HA instance, verify HACS → menu → Custom repositories →
  this repository URL → Integration → Add. Enable beta versions if needed,
  download the prerelease, and verify setup/dashboard, upgrade and removal.
- [ ] Confirm downloaded component hashes match the release. This is separate
  from the completed manual-copy/software simulation evidence.
- [ ] Enable private vulnerability reporting if available and add truthful
  workflow badges after the relevant remote runs exist.

## Later, optional default-store submission

- [ ] Gather independent field evidence before advertising any profile as verified.
- [ ] Review current HACS inclusion requirements and seek the owner's separate
  default-store submission decision.
- [ ] Integration-local brand assets are bundled. A Home Assistant brands PR is
  a separate public action if wanted; this candidate does not submit one.

Official references:

- [HACS integration requirements](https://www.hacs.xyz/docs/publish/integration/)
- [HACS validation action](https://www.hacs.xyz/docs/publish/action/)
- [HACS default-store inclusion](https://www.hacs.xyz/docs/publish/include/)

For current evidence, consult TESTING.md and the private finalization report.
