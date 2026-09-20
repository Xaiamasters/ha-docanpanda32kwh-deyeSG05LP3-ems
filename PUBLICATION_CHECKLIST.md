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

- [x] Start this separate public edition with clean Git history and a GitHub
  noreply commit email. Never push the private repository's history.
- [x] Create this separate repository with Issues enabled, an accurate read-only
  beta description and topics such as `home-assistant`, `hacs`, `deye`, `docan`,
  `energy-monitoring` and `solar`.
- [x] Push only the sealed public tree. Verify the remote commit and inspect
  hassfest, software-tests and HACS workflow outcomes. Fix actual failures;
  do not ignore checks or show passing badges for checks that did not run.
- [x] Publish GitHub prerelease `0.4.0-beta.1` with its matching source/release ZIP,
  checksum, changelog and experimental hardware limitations.
- [x] In a disposable HA instance, verify HACS → menu → Custom repositories →
  this repository URL → Integration → Add. Enable beta versions if needed,
  download the prerelease, and verify setup/dashboard, reload and removal.
- [ ] Verify a real version-to-version upgrade when a subsequent release exists.
- [x] Confirm downloaded component hashes match the release. This is separate
  from the completed manual-copy/software simulation evidence.
- [x] Enable private vulnerability reporting if available and add truthful
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

Published on 2026-09-20: [repository](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems) and [prerelease](https://github.com/Xaiamasters/ha-docanpanda32kwh-deyeSG05LP3-ems/releases/tag/0.4.0-beta.1).
Hassfest, HACS validation and software-test workflows passed on the release commit.
The downloaded release ZIP matches its published SHA-256. Actual HACS 2.0.5 UI
installation passed in a disposable HA instance: all 224 component files matched
the release, and five HACS-generated gzip files decompressed to identical source
assets. Native HA config-flow setup created 31 read-only entities, its dashboard
and a simulated forecast plan. Reload and native config-entry removal passed;
zero domain services were registered. No physical equipment was contacted.

A version-to-version upgrade cannot yet be tested because this is the first
public release. The immutable release tag retains its original checklist;
these completed results are documented in the follow-up on the default branch.

For current evidence, consult TESTING.md and the private publication report.
