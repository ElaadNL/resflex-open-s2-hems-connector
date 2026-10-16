<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# Mock Resource Manager

The `mock_rm.py` script simulates an S2 Resource Manager device for testing any S2 CEM implementation. It handles pairing, WebSocket connections, PEBC instructions, and measurement reporting.

## Quick start

```bash
# Create a virtual environment
python -m venv .venv

# Activate the venv:
source .venv/bin/activate   # Linux/macOS
.venv\Scripts\activate    # Windows

# Install dependencies
pip install "s2-python[ws]" requests urllib3

# Run the mock RM
python mock_rm.py --cem-url https://<cem-ip>:<port> --no-verify-ssl
```

You are prompted for the CEM URL if not provided, then guided through pairing.

TLS is mandatory for S2. Always use `https://`. For self-signed or development certificates, add `--no-verify-ssl` to skip certificate validation.

## CLI reference

|Option|Default|Description|
|--------|---------|-------------|
|`--cem-url`|(prompted)|CEM base URL|
|`--pairing-path`|`/api/s2_pebc/pairing`|Pairing API base path|
|`--connection-path`|`/api/s2_pebc/connection`|Connection API base path|
|`--name`|`Mock RM`|Device name sent in ResourceManagerDetails|
|`--brand`|`MockCo`|Manufacturer brand|
|`--model`|`MockRM-XXXX`|Model string (random suffix)|
|`--interval`|`3.0`|Seconds between PowerMeasurement messages|
|`--power`|`5000.0`|Base power (sets both min and max if not specified)|
|`--min-power`|`--power`|Minimum power in Watts (negative = production)|
|`--max-power`|`--power`|Maximum power in Watts (positive = consumption)|
|`--quantities`|`L1 L2 L3`|Commodity quantities to advertise|
|`--abnormal-ranges`|`false`|Include abnormal condition ranges (+20%)|
|`--log-interval`|`0` (off)|Send device log frame every N measurement ticks|
|`--node-id`|(auto)|UUID for this RM (persisted in state file)|
|`--access-token`|(paired)|Skip pairing if you already have a token|
|`--no-verify-ssl`|`false`|Disable SSL certificate verification|
|`--state-file`|`mock_rm_state.json`|Persist node ID and access token|
|`--long-poll`|`false`|Use long-polling pairing (CEM is the initiator, RM has no HTTP server)|
|`--pairing-token`|(prompted)|Pairing token, skips the prompt. With `--long-poll` it is the RM's own static code (min 6 chars)|

## Device logs

When `--log-interval` is set, the mock RM sends `{"type": "log", ...}` frames over the WebSocket at the specified interval. Levels rotate between INFO and WARN. The CEM acknowledges each frame with an S2 ReceptionStatus.

```bash
# Send a log frame every 5 measurement ticks
python mock_rm.py --cem-url https://<cem-ip>:<port> --no-verify-ssl --log-interval 5
```

## Example devices

### EV Charger (3-phase symmetric, 11 kW, charge only)

```bash
python mock_rm.py --cem-url https://<cem-ip>:<port> --name "EV Charger" --brand MockCo --model "EV-0001" --interval 5 --min-power 0 --max-power 11000 --quantities ELECTRIC.POWER.3_PHASE_SYMMETRIC --state-file mock_rm_ev.json --no-verify-ssl
```

### Home Battery (per-phase, 5 kW charge/discharge, grid support)

```bash
python mock_rm.py --cem-url https://<cem-ip>:<port> --name "Home Battery" --brand MockCo --model "BAT-0001" --interval 3 --min-power -5000 --max-power 5000 --quantities ELECTRIC.POWER.L1 ELECTRIC.POWER.L2 ELECTRIC.POWER.L3 --abnormal-ranges --state-file mock_rm_battery.json --no-verify-ssl
```

### Solar Inverter (per-phase, 6 kW, production only)

```bash
python mock_rm.py --cem-url https://<cem-ip>:<port> --name "Solar Inverter" --brand MockCo --model "PV-0001" --interval 2 --min-power -6000 --max-power 0 --quantities ELECTRIC.POWER.L1 ELECTRIC.POWER.L2 ELECTRIC.POWER.L3 --state-file mock_rm_solar.json --no-verify-ssl
```

### Heat Pump (single-phase L1, 3 kW, consumption only)

```bash
python mock_rm.py --cem-url https://<cem-ip>:<port> --name "Heat Pump" --brand MockCo --model "HP-0001" --interval 10 --min-power 0 --max-power 3000 --quantities ELECTRIC.POWER.L1 --state-file mock_rm_heatpump.json --no-verify-ssl
```

## Pairing flow

1. Script calls `POST /pairing/preparePairing` with device info
2. CEM responds, shows a pairing token in its UI
3. Paste the token at the prompt, or pass it up front with `--pairing-token`
4. Script calls `POST /pairing/requestPairing` with HMAC challenge-response
5. Script calls `POST /pairing/requestConnectionDetails` to get an `accessToken` and WebSocket URL
6. Script calls `POST /pairing/finalizePairing` to confirm

The node ID and access token are saved to `--state-file` so subsequent runs skip pairing.

## Long-polling mode

For an RM without an HTTP server, the CEM is the initiator. Use `--long-poll`
with `--pairing-token` (the RM's static code, min 6 chars):

```bash
python mock_rm.py --cem-url https://<cem-ip>:<port> --long-poll --pairing-token ABC123 --no-verify-ssl
```

The script polls `POST /waitForPairing` in a loop and reacts to CEM actions:

| action | RM behaviour |
| --- | --- |
| `sendNodeDescription` | re-poll with node/endpoint descriptions |
| `preparePairing` | log "show pairing code" |
| `cancelPreparePairing` | log "hide pairing code" |
| `requestPairing` | run the pairing flow with `--pairing-token` |

Start the pairing from the CEM's control API (`/api/initiate-pairing`).

## Reconnection

On disconnect or 401, the script automatically re-pairs or reconnects with a 5-second delay. The access token is rotated on each successful `initiateConnection`.

## Adding new control types

The `MockPEBCControlType` class handles PEBC instructions. To add support for another S2 control type (e.g. FRBC):

1. Create a new class extending the appropriate s2-python base
2. Add an instance to the `control_types` list in `_run_reconnect_loop()`

```python
control_types = [MockPEBCControlType(args), MockFRBCControlType(args)]
```

## Configuration

- `ABORT_CHANCE` (top of file): probability the RM randomly aborts an instruction (0.0 = never, 1.0 = always). Useful for testing CEM retry logic.
