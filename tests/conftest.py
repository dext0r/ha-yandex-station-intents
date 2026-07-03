import pytest


@pytest.fixture(autouse=True)
def enable_custom_integrations(enable_custom_integrations: None) -> None:
    return enable_custom_integrations
