"""Rate-limit rules must aggregate dynamic resource paths into one bucket."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.middleware import rate_limiter
from app.middleware.rate_limiter import RateLimitMiddleware, _TokenBucketStore, _find_rule


def test_recognition_rule_has_a_stable_bucket_key() -> None:
    assert _find_rule("POST", "/images/first/recognize") == ("/recognize", 5, 0.33)
    assert _find_rule("POST", "/images/second/recognize") == ("/recognize", 5, 0.33)


def test_recognition_limit_applies_across_distinct_image_ids(monkeypatch) -> None:
    monkeypatch.setenv("HND_ENVIRONMENT", "development")
    monkeypatch.setattr(rate_limiter, "_store", _TokenBucketStore())
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/images/{image_id}/recognize")
    def recognize(image_id: str) -> dict[str, str]:
        return {"image_id": image_id}

    with TestClient(app) as client:
        headers = {"X-Forwarded-For": "198.51.100.7"}
        for index in range(5):
            assert client.post(f"/images/{index}/recognize", headers=headers).status_code == 200
        response = client.post("/images/sixth/recognize", headers=headers)

    assert response.status_code == 429
    assert response.headers["Retry-After"] == "3"


def test_spoofed_forwarded_for_does_not_reset_a_client_bucket(monkeypatch) -> None:
    monkeypatch.setenv("HND_ENVIRONMENT", "development")
    monkeypatch.setattr(rate_limiter, "_store", _TokenBucketStore())
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/auth/login")
    def login() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        for index in range(5):
            headers = {
                "X-Real-IP": "198.51.100.9",
                "X-Forwarded-For": f"203.0.113.{index}",
            }
            assert client.post("/auth/login", headers=headers).status_code == 200
        response = client.post(
            "/auth/login",
            headers={"X-Real-IP": "198.51.100.9", "X-Forwarded-For": "203.0.113.99"},
        )

    assert response.status_code == 429


def test_redis_errors_fall_back_to_in_memory_buckets(monkeypatch) -> None:
    class _BoomRedis:
        def incr(self, key: str) -> int:
            raise ConnectionError("redis down")

        def expire(self, key: str, window: int) -> None:
            raise ConnectionError("redis down")

    monkeypatch.setenv("HND_ENVIRONMENT", "development")
    monkeypatch.setattr(rate_limiter, "_store", _TokenBucketStore())
    monkeypatch.setattr(rate_limiter, "_redis_client", _BoomRedis())
    monkeypatch.setattr(rate_limiter, "_redis_init_attempted", True)
    app = FastAPI()
    app.add_middleware(RateLimitMiddleware)

    @app.post("/auth/login")
    def login() -> dict[str, bool]:
        return {"ok": True}

    with TestClient(app) as client:
        headers = {"X-Real-IP": "198.51.100.10"}
        for _ in range(5):
            assert client.post("/auth/login", headers=headers).status_code == 200
        response = client.post("/auth/login", headers=headers)

    assert response.status_code == 429
