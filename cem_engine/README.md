<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# CEM Engine

Standalone S2 Customer Energy Manager. Runs `s2_cem_lib` as an HTTPS server that S2 Resource Managers pair with and connect to.

TLS is mandatory. The engine auto-generates a self-signed certificate on first run, or you can provide your own.

## Quick start

Install `s2_cem_lib` first (from PyPI or from the repo: `pip install -e ../s2_cem_lib`), then:

```bash
cd cem_engine
python run.py --port 8443 --pairing-token test1234
```

`--pairing-token` sets a fixed token for testing. Omit it for random tokens per S2 Connect spec.

Optional: `pip install cryptography` if you want auto-generated self-signed certs instead of providing your own.

On first run a self-signed TLS certificate is generated automatically. For production, provide your own:

```bash
python run.py --port 8443 --tls-cert cert.pem --tls-key key.pem
```

Or generate a self-signed cert with OpenSSL:

```bash
openssl req -sha256 -newkey rsa:4096 -nodes -keyout key.pem -x509 -days 7300 -out cert.pem
```

## How it works

1. The engine starts an HTTPS server with the S2 Connect API (`/api/s2_pebc/...`)
2. An S2 Resource Manager (e.g. the [mock RM](../mock_rm/)) pairs via the S2 pairing protocol
3. Once paired, the RM opens a WebSocket and the S2 PEBC session begins
4. The RM sends `PowerConstraints`, `EnergyConstraints`, and `PowerMeasurement` messages
5. The CEM sends `PEBC.Instruction` messages to control the RM
6. RMs can also send device log frames (`{"type": "log", ...}`) - WARN/ERROR logs are mirrored to the engine console

## Control API

When running, the engine exposes a control API on the same port. The `curl` examples work with Linux and Windows Command Prompt

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/api/nodes` | List all connected RMs |
| `GET` | `/api/nodes/{node_id}` | Detail for one RM (constraints, measurements) |
| `GET` | `/api/pairing-token` | Generate a fresh one-time pairing token |
| `POST` | `/api/send-instruction` | Send a PEBC.Instruction to an RM |
| `POST` | `/api/revoke-instruction` | Revoke a previously sent instruction |
| `POST` | `/api/initiate-pairing` | Trigger long-poll pairing with an RM (CEM as initiator) |
| `POST` | `/api/longpoll-action` | Send a raw long-poll action (preparePairing, requestPairing, etc.) |

### Get a pairing token

```bash
curl -sk https://localhost:8443/api/pairing-token
```

Returns a fresh single-use token (5 minute expiry). Call before each pairing attempt.

### List RMs

```bash
curl -sk https://localhost:8443/api/nodes
```

Returns a JSON array of connected RMs with their node IDs, names, control types, and latest measurements.

### RM detail

```bash
curl -sk https://localhost:8443/api/nodes/<node-uuid>
```

Returns full details for one RM: power constraints, energy constraints, and latest measurement.

### Send an instruction

```bash
curl -sk -X POST https://localhost:8443/api/send-instruction -H "Content-Type: application/json" -d "{\"node_id\":\"<node-uuid>\",\"power_envelopes\":[{\"commodity_quantity\":\"ELECTRIC.POWER.L1\",\"upper_limit\":500,\"lower_limit\":0}],\"duration\":3600000}"
```

- `duration` is in milliseconds, defaults to 1 hour (3,600,000)
- `power_constraints_id` auto-generates if not provided
- `abnormal_condition` defaults to `false`

### Revoke an instruction

```bash
curl -sk -X POST https://localhost:8443/api/revoke-instruction -H "Content-Type: application/json" -d "{\"node_id\":\"<node-uuid>\",\"instruction_id\":\"<instruction-uuid>\"}"
```

### Initiate long-poll pairing

For an RM without an HTTP server (long-polling), the CEM starts the pairing:

```bash
curl -sk -X POST https://localhost:8443/api/initiate-pairing \
  -H "Content-Type: application/json" \
  -d '{"clientNodeId":"<node-uuid>","pairingToken":"ABC123"}'
```

`pairingToken` is the RM's static code (min 6 chars). The CEM stores it for the
HMAC check and wakes the polling RM with the `requestPairing` action.

Send other actions (`preparePairing`, `cancelPreparePairing`,
`sendNodeDescription`) with `/api/longpoll-action`:

```bash
curl -sk -X POST https://localhost:8443/api/longpoll-action \
  -H "Content-Type: application/json" \
  -d '{"clientNodeId":"<node-uuid>","action":"preparePairing"}'
```

The RM must be actively polling for the action to be delivered.

## Architecture

```text
cem_engine/run.py
    |
    v
s2_cem_lib/          <- CEM library (pairing, WebSocket, PEBC state machine)
    |
    v
s2-python            <- S2 protocol message models (PyPI: s2-python)
```

The engine is a thin wrapper around `s2_cem_lib.S2CemApp`. It provides:

- File-backed persistence for paired nodes (`state/paired_nodes.json`), so a restart keeps them. A real HEMS can swap the `Persistence` port for a database or host store.
- Console event bus (logs events to stdout)
- Console host env (prints notifications)
- Control API for sending and revoking instructions

## Testing with the mock RM

Start the mock RM in another terminal (from the repo root):

```bash
cd mock_rm
python mock_rm.py --cem-url https://localhost:8443 --no-verify-ssl --power 5000
```

The mock RM will prompt for a pairing token, you'll find it in the CEM engine's console output.
