"""Startup warm-up of the retrieval models."""

from unittest.mock import patch

import pytest

from app.warmup import warm_up_models

pytestmark = pytest.mark.unit


def test_warm_up_runs_one_search_and_reports_the_time(monkeypatch):
    monkeypatch.setenv("WARM_UP_MODELS", "1")
    with patch("app.kb.search_knowledge_base") as search:
        took = warm_up_models()
    search.assert_called_once()
    assert took is not None and took >= 0


def test_warm_up_can_be_switched_off(monkeypatch):
    monkeypatch.setenv("WARM_UP_MODELS", "0")
    with patch("app.kb.search_knowledge_base") as search:
        assert warm_up_models() is None
    search.assert_not_called()


def test_a_failing_warm_up_does_not_stop_the_service_starting(monkeypatch, caplog):
    monkeypatch.setenv("WARM_UP_MODELS", "1")
    with patch("app.kb.search_knowledge_base", side_effect=RuntimeError("database not ready")):
        assert warm_up_models() is None
    assert "warm-up failed" in caplog.text


def test_the_api_warms_up_when_it_starts(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app

    with patch("app.main.warm_up_models") as warm:
        with TestClient(app):
            pass
    warm.assert_called_once()
