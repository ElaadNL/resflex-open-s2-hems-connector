<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/).

---

## [1.0.0] – 2026-10-19

Initial public release, developed under the ElaadNL
[Residential Flexibility](https://elaad.nl/en/projects/residential-flexibility/)
initiative.

### Added

- `s2_cem_lib`: reusable S2 Customer Energy Manager (CEM) library with
  [S2 Connect v1.0](https://docs.s2standard.org/s2-connect/1.0.0/discovery-pairing-authentication/) pairing, WebSocket transport, and the PEBC control type
- `cem_engine`: standalone CEM runner
- `mock_rm`: mock Resource Manager for testing
- Home Assistant example integration under `examples/homeassistant/`
- Integration guide for embedding `s2_cem_lib` in other platforms
