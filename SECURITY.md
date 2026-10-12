# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in this project, please report it
privately rather than opening a public issue.

- Preferred: open a [GitHub Security Advisory](https://github.com/ChiefGyk3D/dell-battery-balance/security/advisories/new)
  for this repository.
- Alternative: contact the maintainer directly through their GitHub profile
  ([@ChiefGyk3D](https://github.com/ChiefGyk3D)).

Please include:

- A description of the vulnerability and its potential impact.
- Steps to reproduce it, including the Dell Latitude model and firmware
  version involved, if relevant.
- Any suggested mitigation, if known.

## Scope

This repository provides a CLI and service that read and set battery
charge-threshold and wear-related values on Dell Latitude hardware via
`dell-wmi-sysman` sysfs attributes. Vulnerabilities in the Linux kernel's
`dell-wmi-sysman` driver itself, or in the system firmware/BIOS, should be
reported to those projects/Dell directly.

## Response

This is a community-maintained project without a formal SLA. Reports are
reviewed as soon as reasonably possible, and a fix or mitigation is shipped
once the issue is confirmed.
