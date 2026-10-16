# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Root conftest: mock homeassistant if not installed.

The integration's __init__.py imports homeassistant at module level,
which means any submodule import (pairing_models, token, etc.) would
also require homeassistant to be importable. When running tests via
`coverage run` or any Python environment without homeassistant installed,
this provides lightweight mocks so unit tests can run without a full HA install.
"""
import sys
from unittest.mock import MagicMock

try:
    import homeassistant  # noqa: F401
except ImportError:
    _HA_MODULES = [
        "homeassistant",
        "homeassistant.config_entries",
        "homeassistant.core",
        "homeassistant.components",
        "homeassistant.components.http",
        "homeassistant.components.persistent_notification",
        "homeassistant.helpers",
        "homeassistant.helpers.event",
        "homeassistant.helpers.network",
        "homeassistant.util",
        "homeassistant.util.dt",
    ]
    for _mod in _HA_MODULES:
        sys.modules[_mod] = MagicMock()
