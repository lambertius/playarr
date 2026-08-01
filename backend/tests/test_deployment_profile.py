from types import SimpleNamespace

import pytest

from app.services.deployment_profile import (
    UnsafeDeploymentProfile,
    configured_web_workers,
    validate_deployment_profile,
)


def _settings(profile="single_process"):
    return SimpleNamespace(deployment_profile=profile, redis_url="redis://example/0")


def test_single_process_refuses_multiple_web_workers():
    with pytest.raises(UnsafeDeploymentProfile, match="exactly one"):
        validate_deployment_profile(
            _settings(), environment={"WEB_CONCURRENCY": "2"},
        )


def test_single_process_accepts_one_writer_process():
    result = validate_deployment_profile(
        _settings(), environment={"UVICORN_WORKERS": "1"},
    )
    assert result == {
        "profile": "single_process", "web_workers": 1, "redis_reachable": None,
    }


def test_redis_profile_refuses_silent_local_fallback():
    with pytest.raises(UnsafeDeploymentProfile, match="reachable"):
        validate_deployment_profile(
            _settings("redis"), environment={}, probe_redis=lambda _url: False,
        )
    assert validate_deployment_profile(
        _settings("redis"), environment={}, probe_redis=lambda _url: True,
    )["redis_reachable"] is True


def test_gunicorn_worker_argument_is_detected():
    assert configured_web_workers({"GUNICORN_CMD_ARGS": "--workers 3 --timeout 30"}) == 3
