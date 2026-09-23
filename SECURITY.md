# Security Policy

## Supported versions

Only the latest release receives fixes. Please update through HACS before
reporting an issue.

| Version          | Supported          |
| ---------------- | ------------------ |
| Latest (0.3.x)   | :white_check_mark: |
| Older releases   | :x:                |

## Reporting a vulnerability

Please **do not open a public issue** for security problems.

Report them privately through GitHub:
[Security → Report a vulnerability](https://github.com/3615nulsi/ha-tam-montpellier/security/advisories/new).

- You should get a first answer within **7 days**.
- If the report is confirmed, a fixed release is published as soon as
  possible, and the advisory is made public once users can update. You will be
  credited unless you prefer otherwise.
- If it is declined, you will get an explanation.

This is a hobby project maintained on free time; thank you for your patience.

## Scope

This integration only reads public TaM open data feeds (GTFS / GTFS-RT) and
serves a Lovelace card from your Home Assistant instance. Issues in Home
Assistant itself, HACS or the TaM feeds should be reported to their
respective projects.
