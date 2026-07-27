"""The key seed ships with the base image and must stay inert until a variant asks for it.

Both variants here run the same entrypoint as the seeded one with the UOS_SEED_*
flags unset, so a key appearing at the contract path is the seed firing when
nothing turned it on.
"""

import pytest

from unifi_containers import testing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(1800)]


def test_no_api_key_is_published(uos_without_seed):
    assert testing.read_file(uos_without_seed, testing.UOS_API_KEY_FILE) is None
