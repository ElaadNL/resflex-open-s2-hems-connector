<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# Home Assistant S2 PEBC Integration

Example Home Assistant integration built on `s2_cem_lib`. Implements the S2 Customer Energy Manager (CEM) with Power Envelope Based Control (PEBC).

TLS is mandatory. The docker compose mounts TLS certificates from `docker/mount/`.

## TLS setup

TLS is required by the S2 spec. Generate a key and certificate before starting the container:

```bash
openssl req -sha256 -newkey rsa:4096 -nodes -days 3650 -keyout docker/mount/privkey.pem -x509 -out docker/mount/fullchain.pem
```

Run this from `examples/homeassistant/`. Set the Common Name to the hostname or IP that Resource Managers will use to connect.

`docker/mount/*.pem` is git-ignored. See the TLS / WSS section in the [root README](../../README.md) for more details.

## Quick start

Start a Home Assistant instance with the S2 PEBC CEM integration pre-configured via Docker Compose:

```bash
cd examples/homeassistant/docker
docker compose up -d
```

Open Home Assistant at `https://localhost:8123`, complete onboarding, then go to
**Settings > Devices & Services > Add Integration** and search for "S2 PEBC".

### DNS-SD / LAN discovery

The integration advertises `_s2connect._tcp` via mDNS so Resource Managers can discover
this CEM on the local network.

- **Linux (native)**: works out of the box with `network_mode: host` in docker-compose.
- **Docker Desktop (Windows/macOS)**: DNS-SD inside Docker cannot reach the LAN because
  the VM doesn't share the host's mDNS stack. Therefore, you will need to manually add the IP of the
  Home Assistant CEM to your Resource Manager.

For detailed documentation see `docs/s2-ha-integration.md`.

## Long-poll pairing

For Resource Managers without an HTTP server, the CEM initiates pairing. Call the
`initiate_pairing` service (Developer Tools -> Actions) with:

- `Node ID`: the RM's UUID
- `Pairing token`: the RM's static pairing code (min 6 chars)

The RM must be actively polling `waitForPairing` for the action to be delivered.

## Development

The integration depends on `s2_cem_lib` as an editable install.
Install from the repo root so setuptools can resolve relative paths:

```bash
cd ../..                        # back to repo root
pip install -e s2_cem_lib       # editable install of the CEM library
cd examples/homeassistant
pip install -e '.[dev]'         # editable install + dev deps (pytest, ruff)
```

Run tests and linting:

```bash
pytest
ruff check
```
