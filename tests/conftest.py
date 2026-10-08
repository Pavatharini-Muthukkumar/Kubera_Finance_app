import pytest

from kubera.config import Config


@pytest.fixture
def config(tmp_path):
    return Config(
        owner_names=["Jane Doe"],
        owner_ibans={"DE89370400440532013000", "DE02120300000000202051"},
        account_names={"DE89370400440532013000": "Main account"},
        state_dir=tmp_path / ".kubera",
    )
