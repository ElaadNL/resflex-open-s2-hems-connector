<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# resflex-open-s2-hems-connector

S2 Customer Energy Manager (CEM) reference implementation. Contains the CEM library, a standalone engine, and example integrations. All following the [S2 protocol](https://docs.s2standard.org/).

Supports:

- Control type: Power Envelope Based Control (PEBC)
- Pairing: S2 Connect specification
- TLS is mandatory. All connections use HTTPS/WSS, even on localhost.

## Disclaimer

This software is an open reference implementation, developed under the
coordination of ElaadNL as part of the
[Residential Flexibility](https://elaad.nl/en/projects/residential-flexibility/) initiative. It is not a finished,
certified, or end-user product. Before publication, the code was reviewed for
security by the European Network for Cyber Security (ENCS); that review is a
snapshot in time, not a guarantee. Anyone who integrates this software into a
product does so at their own risk and is responsible for its secure integration,
ongoing security testing, regulatory compliance, and any certification of that
product. The software is provided "as is", without warranty, under the Apache
License 2.0.

Read the full [disclaimer](DISCLAIMER.md) before using this software.

## What's in this repo

```text
s2_cem_lib/               CEM library: pairing, WebSocket, PEBC state machine
cem_engine/               Standalone CEM engine: run.py + README
mock_rm/                  Mock Resource Manager for testing
    README.md
examples/
    homeassistant/         Home Assistant integration example
        src/               HA custom component
        docker/            Docker Compose + TLS config
        tests/             HA integration tests
        README.md
docs/
    s2-ha-integration.md   HA-specific deep dive
    integration-guide.md   How to embed s2_cem_lib into any platform
```

`s2_cem_lib` is the core. Everything else consumes it.

| Component | Doc |
|-----------|---|
| CEM library | `s2_cem_lib/` (source) |
| Standalone engine | `cem_engine/README.md` |
| Mock RM | `mock_rm/README.md` |
| HA integration | `examples/homeassistant/README.md` |

## Home Assistant integration

The HA integration lives in `examples/homeassistant/`. It's a reference for building your own integration on top of `s2_cem_lib`.

### TLS / WSS

The S2 spec requires TLS. No plain HTTP, even on localhost.

Generate a key and certificate before starting the container:

```bash
openssl req -sha256 -newkey rsa:4096 -nodes -days 3650 -keyout examples/homeassistant/docker/mount/privkey.pem -x509 -out examples/homeassistant/docker/mount/fullchain.pem
```

Set Common Name to the hostname or IP that Resource Managers will use to connect (e.g. `homeassistant.local` for mDNS, or `192.168.0.231` for direct IP).

These files are git-ignored and must **never** be committed.

If the instance is reachable from the internet, use a certificate issued by a public CA (e.g. [Let's Encrypt](https://letsencrypt.org/)) at those same two paths instead of self-signing.

### Docker setup

```bash
cd examples/homeassistant/docker
docker compose up -d
```

Open HA at `https://localhost:8123` (accept the cert warning if self-signed), complete onboarding. Go to Settings > Devices & Services > Add Integration, search for "S2 PEBC". Also set Settings > System > Network to the same URL to prevent TLS mismatch issues.

### Pairing a Resource Manager

- Settings > Devices & Services > S2 PEBC > Configure shows the pairing URL and token (valid 5 minutes).
- Or the RM can call `preparePairing` itself and a token appears in HA notifications.
- Once paired, the RM gets a WebSocket URL + token and opens a WSS connection.
- Pairing requests are rate limited to 5 per node per minute, and at most 16 offers may be pending. Going over either limit returns HTTP 429, so wait a minute and try again.

## Development

### Python

Windows: [Python 3.13](https://www.python.org/downloads/) with "Add Python to PATH". Use `py -3.13` for all commands.
Linux: `sudo apt install python3.13 python3.13-venv python3-pip`

```bash
python3 --version
```

This project requires **Python 3.13**. Python 3.14 is not yet supported by `s2-python`

### Virtual environment

```bash
# Linux/macOS
python3.13 -m venv .venv
source .venv/bin/activate

# Windows
py -3.13 -m venv .venv
.venv\Scripts\Activate.ps1
```

### Install

```bash
pip install -e s2_cem_lib
cd examples/homeassistant
pip install -e '.[dev]'
```

Installs `s2_cem_lib` from the local repo, then the HA integration and all dev dependencies (pytest, ruff, coverage).

**Windows:** Use `py -3.13` instead of `python` / `python3`.

### Lint

```bash
cd s2_cem_lib
ruff check

cd examples/homeassistant
ruff check
```

### Tests

```bash
# CEM library tests
cd s2_cem_lib
pytest

# HA integration tests
cd examples/homeassistant
pytest
```

### Coverage

```bash
# CEM library
cd s2_cem_lib
coverage run -m pytest
coverage report

# HA integration
cd examples/homeassistant
coverage run -m pytest
coverage report
```
