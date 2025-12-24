"""Pytest configuration."""

import pytest


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"
