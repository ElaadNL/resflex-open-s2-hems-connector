# SPDX-FileCopyrightText: ElaadNL
#
# SPDX-License-Identifier: Apache-2.0

"""Shared fixtures for connection tests imported from pairing.conftest."""
from s2_cem_lib.tests.pairing.conftest import (  # noqa: F401
    RM_DESCRIPTION,
    TEST_S2_VERSION,
    add_paired_node,
    make_app,
    make_hass,
    rejected_status,
    server,
)
