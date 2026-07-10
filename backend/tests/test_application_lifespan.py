"""Application startup invariants independent of optional background workers."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from app import main
from app.services import auth_api


def test_lifespan_initializes_database_when_training_worker_is_disabled(monkeypatch) -> None:
    initialized: list[bool] = []

    def session_factory() -> object:
        initialized.append(True)
        return object()

    monkeypatch.setattr(main, "configure_determinism", lambda _settings: 0)
    monkeypatch.setattr(
        main,
        "get_component_availability",
        lambda: {"training_worker": False},
    )
    monkeypatch.setattr(auth_api, "_default_session_factory", session_factory)

    async def run_lifespan() -> None:
        async with main.lifespan(FastAPI()):
            assert initialized == [True]

    asyncio.run(run_lifespan())
