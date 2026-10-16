<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# S2 Home Assistant Integration

For information on the S2 specification and API, see the [official documentation](https://docs.s2standard.org/) and the [S2 Connect Specification](https://docs.s2standard.org/s2-connect/1.0.0/discovery-pairing-authentication/). This file goes over HA integration specific code and logic. Generic S2 logic and messages are not documented in this file. For information regarding the pairing process and/or setup of the integration, refer to the README file.

## Overview

This integration implements the **S2 PEBC (Power Envelope Based Control)** protocol, allowing Home Assistant to act as a **Central Entity (CEM)**. It enables control over **Resource Manager (RM)** nodes by facilitating pairing, connection management, and instruction delivery via WebSockets.

## Usage

### 1. Installation and Setup

1. In Home Assistant, go to **Settings** > **Devices & Services**.
2. Click **Add Integration** and search for "**S2 PEBC**".
3. Follow the configuration prompts to add the integration.

### 2. Pairing an RM

There are two primary pairing methods, plus a long-poll variant for RMs that do not run an HTTP server:

- **RM-initiated**: The RM sends a pairing request to the CEM. This triggers a notification in Home Assistant. Clicking the notification allows you to view and use the pairing token.
- **CEM-initiated**: Click the **Configure** button on the S2 PEBC integration card in Home Assistant to generate a pairing token. Copy this token into your RM to establish the connection.
- **Long-poll (CEM-initiated)**: For an RM without an HTTP server, call the `initiate_pairing` service with the RM's node ID and its static pairing code (min 6 chars). The RM must be actively polling `waitForPairing` for the action to be delivered.

Once the pairing process is complete, the RM will use its communication token to establish a WebSocket connection, starting an S2 session.

### 3. Controlling Devices

Once paired, RMs appear as devices in Home Assistant. You can interact with them using the following services:

| Service | Description |
| :--- | :--- |
| `unpair_node` | Removes a node from the paired list and closes its connection. |
| `send_instruction` | Sends a PEBC power envelope instruction to an RM node. Can be used via the HA UI, automations, or scripts. |
| `revoke_instruction` | Revokes a previously sent instruction. |
| `initiate_pairing` | Starts long-poll pairing with an RM that has no HTTP server. |

## Core Components

### 1. Pairing & Connection Management

The integration creates an `S2CemApp` instance which manages a `PairingServer` and `S2WebSocketView`.

- **Pairing**: Generates single-use pairing tokens (valid for 5 minutes) to facilitate secure onboarding of RM nodes.
- **Connection**: Once paired, nodes establish a WebSocket connection handled by `S2WebSocketView`.
- **Persistence**: Paired node information is stored via the `Persistence` adapter backed by Home Assistant's `Store` mechanism.

### 2. WebSocket Communication

The integration exposes a WebSocket endpoint at the path defined by `S2_WS_PATH`.

- **Handlers**: The `s2_cem_lib/websocket/handlers/` directory contains specialized handlers for different S2 message types:
  - `handshake.py`: Manages the initial connection setup.
  - `pebc.py`: Handles PEBC-specific logic.
  - `measurements.py`: Processes data reported by RM nodes.
  - `instruction_status.py`: Tracks the status of sent instructions.
  - `resource_manager.py`: Manages RM-specific state and resources.

### 3. Home Assistant Services

The integration exposes several services to allow users/automations to interact with paired nodes:

| Service | Description | Parameters |
| :--- | :--- | :--- |
| `unpair_node` | Removes a node from the paired list and closes its connection. | `node_id` (UUID) |
| `send_instruction` | Sends a PEBC power envelope instruction to an RM node. | `node_id` (UUID), `power_envelopes` (list), `duration` (ms, optional), `abnormal_condition` (bool, optional), `execution_time` (str, optional) |
| `revoke_instruction` | Revokes a previously sent instruction. | `node_id` (UUID), `instruction_id` (UUID, optional) |
| `initiate_pairing` | Starts long-poll pairing with an RM that has no HTTP server. | `node_id` (UUID), `pairing_token` (str, min 6 chars) |

## Implementation Details

### Instruction Validation

Before sending an instruction via `send_instruction`, the integration validates the requested power envelopes against the `PowerConstraints` previously advertised by the RM node. It ensures:

- The `commodity_quantity` is supported by the node.
- The requested `upper_limit` and `lower_limit` fall within the allowed ranges (accounting for `abnormal_condition` flags).

### Security

- **TLS/SSL**: Since S2 requires secure communication (WSS), the integration checks for HA's SSL configuration. If HA is not running with TLS, it logs a warning.
- **Certificate Fingerprinting**: For self-signed certificates, the integration automatically calculates the SHA256 fingerprint to facilitate secure pairing.

### Device Logs

Resource Managers can send log frames over the authenticated WebSocket. Two formats are supported:

**Single log:**

```json
{"type": "log", "id": "<uuid>", "level": "INFO", "message": "some text"}
```

**Batch (max 128 messages):**

```json
{"type": "log_batch", "batch_id": "<uuid>", "messages": [{"id": "...", "level": "WARN", "message": "..."}]}
```

Bathes over 128 messages are rejected with `TEMPORARY_ERROR`.

When a log is received the integration fires a Home Assistant event `s2_pebc_rm_log` with:

- `node_id`: node UUID
- `level`: `INFO`, `WARN`, or `ERROR`
- `message`: log text
- `source`: optional origin string
- `timestamp`: optional timestamp from the RM

WARN and ERROR level messages are mirrored to the HA logger so they appear in the main logs. Each log frame is acknowledged with an S2 `ReceptionStatus`.

To see log events live: go to **Developer Tools -> Events** in Home Assistant, type `s2_pebc_rm_log` and click **Start Listening**. Use the mock RM with `--log-interval` to generate test events.
