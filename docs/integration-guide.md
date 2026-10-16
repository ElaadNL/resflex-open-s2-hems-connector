<!--
SPDX-FileCopyrightText: ElaadNL

SPDX-License-Identifier: Apache-2.0
-->

# S2 CEM Integration Guide

The S2 CEM library handles pairing, WebSocket connections, PEBC state machines, and instruction sending. This guide explains how to plug it into a HEMS.

The only hard dependency is **aiohttp**. It works with any Python-based HEMS that can host an aiohttp server. Write the four adapter classes below, pass them in, and you have a working S2 PEBC CEM.

For a complete reference implementation, see the Home Assistant integration example at `examples/homeassistant/src/`.

---

## 1. Write the four adapter classes

Your HEMS needs to provide concrete implementations of the interfaces below.

### EventBus

Lets the CEM engine broadcast telemetry and state changes to your HEMS.

```python
class EventBus(Protocol):
    def send(self, signal: str, *args: Any) -> None: ...
```

### Persistence

Stores paired node configurations so they survive restarts. Plain dicts so use whatever storage your HEMS has.

```python
class Persistence(Protocol):
    async def load(self) -> dict | None: ...
    async def save(self, data: dict) -> None: ...
    async def remove(self) -> None: ...
```

### HostEnv

Lets the engine surface notifications in your HEMS.

```python
class HostEnv(Protocol):
    def notify(self, notification_id: str, title: str, message: str) -> None: ...
    def dismiss(self, notification_id: str) -> None: ...
```

### TaskScheduler

Spawns background work and schedules delayed callbacks. The engine never touches your event loop directly.

```python
class TaskScheduler(Protocol):
    def create_task(self, coro: Awaitable[None]) -> None: ...
    def call_later(self, delay_seconds: float, cb: Callable[[], None]) -> Callable[[], None]: ...
```

---

## 2. Create the CEM app

```python
from s2_cem_lib import S2CemApp, EventBus, HostEnv, Persistence, TaskScheduler
from s2_cem_lib.pairing.pairing_models import (
    CommunicationProtocol,
    Deployment,
    EndpointDescription,
    Role,
    S2NodeDescription,
)

node_desc = S2NodeDescription(
    id=uuid.uuid4(),
    brand="My HEMS",
    type="CEM",
    modelName="S2 CEM",
    role=Role.CEM,
)

endpoint = EndpointDescription(deployment=Deployment.LAN)

app = S2CemApp(
    node_desc,
    persistence=MyPersistence(),
    event_bus=MyEventBus(),
    host_env=MyHostEnv(),
    scheduler=MyTaskScheduler(),
    endpoint_description=endpoint,
    supported_communication_protocols=[CommunicationProtocol.WEBSOCKET],
    supported_s2_message_versions=["0.0.2-beta"],
)
```

---

## 3. Start and register routes

```python
await app.start()
app.register_routes(your_aiohttp_app)
```

On shutdown:

```python
await app.stop()
```

The engine handles pairing handshakes, S2 WebSocket lifecycle, PEBC instruction delivery, and RM state tracking on its own.
