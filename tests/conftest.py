from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

os.environ.setdefault("APP_ENV", "test")
os.environ.setdefault("LLM_PROVIDER", "fake")
os.environ.setdefault("EMBEDDINGS_PROVIDER", "fake")
os.environ.setdefault("SPEECH_PROVIDER", "fake")
os.environ.setdefault("RERANKER", "none")
os.environ.setdefault("API_KEYS", "test-key,second-key")
os.environ.setdefault("ADMIN_API_KEY", "test-admin")
os.environ.setdefault("RATE_LIMIT_PER_MINUTE", "1000")

from harbor.config import Settings  # noqa: E402
from harbor.container import build_offline_container  # noqa: E402


@pytest.fixture(scope="session")
def settings() -> Settings:
    return Settings(_env_file=None)


@pytest.fixture()
def container(settings: Settings):
    return build_offline_container(settings)


@pytest.fixture()
def client(settings: Settings, container):
    from harbor.api.app import create_app

    app = create_app(settings, container=container)
    with TestClient(app) as c:
        yield c
