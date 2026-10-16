# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Constants for the S2 PEBC Integration."""

DOMAIN = "s2_pebc_integration"

# S2 message versions this CEM supports.
S2_SUPPORTED_VERSIONS = ["0.0.2-beta", "0.0.1-beta"] # Make sure these are in DESCENDING order (Newest version first)

# URL paths
BASE_PATH = "/api/s2_pebc"
S2_WS_PATH = f"{BASE_PATH}/ws"
S2_PAIRING_PATH = f"{BASE_PATH}/pairing"       # pairing API base path
S2_CONNECTION_PATH = f"{BASE_PATH}/connection"  # connection API base path

# S2 Connect API major version supported by this CEM.
S2_CONNECT_API_VERSION = "v1"

# How long a pairing token is valid.
PAIRING_TOKEN_TIMEOUT_MINUTES = 5

# Timeout for a fixed-value pairing token (--pairing-token).
# Only to be used for testing
STATIC_PAIRING_TOKEN_TIMEOUT_DAYS = 365

# The number of bytes to show in a pairing token for self-signed certificates, at least 12, at most 32
PAIRING_TOKEN_FINGERPRINT_LENGTH = 12

# How long a communication token is valid 
COMM_TOKEN_TIMEOUT_SECONDS = 30

# How long an access token is valid. Tokens rotate on every reconnect, so this is the longest a device can 
# stay offline and still reconnect without re-pairing. 
# The S2 Connect spec allows up to 5 years, we deliberately use 1 year to improve the security of the library.
# The shorter a token lives, the less it is worth to anyone who gets hold of it so it is recommended to keep this as short as possible.
ACCESS_TOKEN_TIMEOUT_DAYS = 365

# How long a pairing attempt remains valid
PAIRING_ATTEMPT_TIMEOUT_SECONDS = 15

# How long a pending access token from initiateSession remains valid
PENDING_TOKEN_TIMEOUT_SECONDS = 15

# Max pairing requests (preparePairing and requestPairing) per node per minute.
REQUEST_PAIRING_RATE_LIMIT = 5

# Time window for rate limiting pairing requests
RATE_LIMIT_WINDOW_SECONDS = 60

# Max distinct nodes tracked by the pairing rate limiter at once.
MAX_RATE_LIMIT_KEYS = 1000

# Max pending pairing offers held at once.
MAX_PENDING_PAIRING_OFFERS = 16

# Default duration for PEBC instructions if not specified (1 hour).
DEFAULT_INSTRUCTION_DURATION_MS = 3_600_000

# Max backoff retry delay for instructions that were ABORTED or REJECTED and also for TEMPORARY_ERROR retries.
MAX_BACKOFF_RETRY_DELAY_SECONDS = 300 # 5 minutes

# Max PEBC.PowerConstraints kept per node. Prevents RM from sending too many constraints
MAX_POWER_CONSTRAINTS_PER_NODE = 64

# Max PEBC.EnergyConstraint kept per node.
MAX_ENERGY_CONSTRAINTS_PER_NODE = 64

# Max forecast elements kept per node. The furthest elements are dropped first.
MAX_FORECAST_ELEMENTS_PER_NODE = 512

# DISPATCHER SIGNALS
# Occurs when RM sends ResourceManagerDetails.
SIGNAL_NODE_CONNECTED = f"{DOMAIN}_node_connected"

# Occurs when a new PowerMeasurement is received from a RM
SIGNAL_POWER_MEASUREMENT_FMT = f"{DOMAIN}_{{node_id}}_measurement"

# Occurs when an RM WebSocket disconnects.
SIGNAL_NODE_DISCONNECTED_FMT = f"{DOMAIN}_{{node_id}}_disconnected"

# Occurs when PEBC.PowerConstraints are received or revoked.  Args: (node_id, rm_details, constraints_dict)
SIGNAL_POWER_CONSTRAINTS = f"{DOMAIN}_power_constraints"

# Occurs when PEBC.EnergyConstraint is received or revoked.  Args: (node_id, rm_details, constraints_dict)
SIGNAL_ENERGY_CONSTRAINTS = f"{DOMAIN}_energy_constraints"

# Occurs when an RM sends a log message.  Args: (node_id, level, message, source, timestamp)
SIGNAL_RM_LOG = f"{DOMAIN}_rm_log"

POWER_ENVELOPE_CONTROL_TYPE = "POWER_ENVELOPE_BASED_CONTROL"