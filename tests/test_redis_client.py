"""
Tests for the shared Redis connection pool (``api.services.redis_client``)
and the stores that depend on it (``job_store``, ``user_store``).

All tests use *fakeredis* so no running Redis server is required.
"""
from __future__ import annotations

import json
import threading
from unittest.mock import patch

import fakeredis
import pytest

import api.services.redis_client as redis_client_mod
from api.services import job_store, user_store
from api.services.redis_client import _get_pool, get_redis_client


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_pool():
    """Reset the module-level pool before and after every test."""
    redis_client_mod._pool = None
    yield
    redis_client_mod._pool = None


@pytest.fixture()
def fake_pool():
    """Patch ``_get_pool`` to return a *fakeredis* connection pool.

    We create a ``FakeRedis`` instance and extract its underlying
    ``ConnectionPool`` so that every ``get_redis_client()`` call in the
    code-under-test shares the same in-memory fake server.
    """
    server = fakeredis.FakeServer()
    fake_redis = fakeredis.FakeRedis(server=server, decode_responses=True)
    pool = fake_redis.connection_pool
    with patch.object(redis_client_mod, "_get_pool", return_value=pool):
        yield pool


# ---------------------------------------------------------------------------
# redis_client module tests
# ---------------------------------------------------------------------------

class TestGetPool:
    """Tests for ``_get_pool`` singleton behaviour."""

    @patch("api.services.redis_client.redis.ConnectionPool.from_url")
    def test_creates_pool_once(self, mock_from_url):
        """The pool is created on first call and reused on subsequent calls."""
        mock_from_url.return_value = object()  # sentinel
        pool_a = _get_pool()
        pool_b = _get_pool()
        assert pool_a is pool_b
        mock_from_url.assert_called_once()

    @patch("api.services.redis_client.redis.ConnectionPool.from_url")
    def test_passes_settings(self, mock_from_url):
        """``from_url`` receives the URL from settings and decode_responses."""
        _get_pool()
        args, kwargs = mock_from_url.call_args
        # First positional arg is the URL
        from api.settings import settings
        assert args[0] == settings.redis_url
        assert kwargs["decode_responses"] is True

    @patch("api.services.redis_client.redis.ConnectionPool.from_url")
    def test_thread_safety(self, mock_from_url):
        """Concurrent threads must not create multiple pools."""
        mock_from_url.return_value = object()
        results: list = []
        barrier = threading.Barrier(4)

        def worker():
            barrier.wait()
            results.append(_get_pool())

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # All threads got the same pool object
        assert len(set(id(r) for r in results)) == 1
        mock_from_url.assert_called_once()


class TestGetRedisClient:
    """Tests for ``get_redis_client``."""

    def test_returns_redis_instance(self, fake_pool):
        client = get_redis_client()
        assert client.connection_pool is fake_pool

    def test_multiple_clients_share_pool(self, fake_pool):
        a = get_redis_client()
        b = get_redis_client()
        assert a.connection_pool is b.connection_pool


# ---------------------------------------------------------------------------
# job_store tests
# ---------------------------------------------------------------------------

class TestJobStore:
    """Integration tests for ``api.services.job_store`` using fakeredis."""

    def test_create_and_get_job(self, fake_pool):
        record = job_store.create_job("j1", user_id="u1", original_filename="v.mp4")
        assert record["job_id"] == "j1"
        assert record["status"] == "pending"
        assert record["progress"] == 0

        fetched = job_store.get_job("j1")
        assert fetched is not None
        assert fetched["job_id"] == "j1"
        assert fetched["user_id"] == "u1"

    def test_get_job_missing(self, fake_pool):
        assert job_store.get_job("nonexistent") is None

    def test_update_job(self, fake_pool):
        job_store.create_job("j2")
        job_store.update_job("j2", status="running", progress=50, message="halfway")
        fetched = job_store.get_job("j2")
        assert fetched["status"] == "running"
        assert fetched["progress"] == 50
        assert fetched["message"] == "halfway"

    def test_update_job_missing_is_noop(self, fake_pool):
        # Should not raise
        job_store.update_job("ghost", status="failed")

    def test_video_cache(self, fake_pool):
        assert job_store.get_video_cache("u1", "abc123") is None
        job_store.set_video_cache("u1", "abc123", "s3://bucket/key")
        assert job_store.get_video_cache("u1", "abc123") == "s3://bucket/key"

    def test_create_job_sets_timestamps(self, fake_pool):
        record = job_store.create_job("j3")
        assert "created_at" in record
        assert "updated_at" in record
        assert record["created_at"] == record["updated_at"]

    def test_update_job_refreshes_updated_at(self, fake_pool):
        record = job_store.create_job("j4")
        original_updated = record["updated_at"]
        job_store.update_job("j4", progress=10)
        fetched = job_store.get_job("j4")
        # updated_at should have changed (or at least be present)
        assert fetched["updated_at"] >= original_updated


# ---------------------------------------------------------------------------
# user_store tests
# ---------------------------------------------------------------------------

class TestUserStore:
    """Integration tests for ``api.services.user_store`` using fakeredis."""

    def test_create_and_get_user(self, fake_pool):
        record = user_store.create_user(
            google_id="g1", email="a@b.com", name="Alice", picture="http://pic"
        )
        assert record["google_id"] == "g1"
        assert record["email"] == "a@b.com"
        assert "user_id" in record

        fetched = user_store.get_user(record["user_id"])
        assert fetched == record

    def test_get_user_by_google_id(self, fake_pool):
        record = user_store.create_user(
            google_id="g2", email="b@c.com", name="Bob", picture="http://pic2"
        )
        fetched = user_store.get_user_by_google_id("g2")
        assert fetched is not None
        assert fetched["user_id"] == record["user_id"]

    def test_get_user_by_google_id_missing(self, fake_pool):
        assert user_store.get_user_by_google_id("nope") is None

    def test_get_user_missing(self, fake_pool):
        assert user_store.get_user("nonexistent") is None

    def test_update_user(self, fake_pool):
        record = user_store.create_user(
            google_id="g3", email="c@d.com", name="Carol", picture="http://pic3"
        )
        user_store.update_user(record["user_id"], name="Caroline", email="new@d.com")
        fetched = user_store.get_user(record["user_id"])
        assert fetched["name"] == "Caroline"
        assert fetched["email"] == "new@d.com"
        # google_id unchanged
        assert fetched["google_id"] == "g3"

    def test_update_user_missing_is_noop(self, fake_pool):
        # Should not raise
        user_store.update_user("ghost", name="Nobody")

    def test_pipeline_used_in_create(self, fake_pool):
        """create_user writes both the user record and the google-id index."""
        record = user_store.create_user(
            google_id="g4", email="d@e.com", name="Dave", picture="http://pic4"
        )
        # Both keys should exist
        r = get_redis_client()
        assert r.get(f"user:{record['user_id']}") is not None
        assert r.get(f"user:google:g4") == record["user_id"]

    def test_pipeline_used_in_update(self, fake_pool):
        """update_user refreshes both the user record and the google-id index."""
        record = user_store.create_user(
            google_id="g5", email="e@f.com", name="Eve", picture="http://pic5"
        )
        user_store.update_user(record["user_id"], name="Evelyn")

        r = get_redis_client()
        raw = r.get(f"user:{record['user_id']}")
        assert raw is not None
        data = json.loads(raw)
        assert data["name"] == "Evelyn"
        # google index still points to same user
        assert r.get("user:google:g5") == record["user_id"]
