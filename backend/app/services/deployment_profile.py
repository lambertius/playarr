"""ARCH-001 validation for the two supported deployment profiles."""
from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping
from typing import Any


class UnsafeDeploymentProfile(RuntimeError):
    pass


def configured_web_workers(environment: Mapping[str, str]) -> int:
    """Resolve common Uvicorn/Gunicorn worker configuration sources."""
    for name in ("UVICORN_WORKERS", "WEB_CONCURRENCY"):
        value = environment.get(name)
        if value:
            try:
                return int(value)
            except ValueError as exc:
                raise UnsafeDeploymentProfile(f"{name} must be an integer") from exc
    gunicorn = environment.get("GUNICORN_CMD_ARGS", "")
    match = re.search(r"(?:--workers(?:=|\s+)|-w\s+)(\d+)", gunicorn)
    return int(match.group(1)) if match else 1


def redis_reachable(redis_url: str) -> bool:
    try:
        import redis
        return bool(redis.Redis.from_url(
            redis_url, socket_connect_timeout=2, socket_timeout=2,
        ).ping())
    except Exception:
        return False


def validate_deployment_profile(
    settings: Any,
    *,
    environment: Mapping[str, str] | None = None,
    probe_redis: Callable[[str], bool] = redis_reachable,
) -> dict[str, Any]:
    """Refuse combinations that bypass the declared mutation topology."""
    environment = environment or os.environ
    workers = configured_web_workers(environment)
    if workers < 1:
        raise UnsafeDeploymentProfile("web worker count must be at least one")

    profile = settings.deployment_profile
    if profile == "single_process":
        if workers != 1:
            raise UnsafeDeploymentProfile(
                "DEPLOYMENT_PROFILE=single_process requires exactly one web worker; "
                f"configured worker count is {workers}"
            )
        if environment.get("PLAYARR_PROCESS_ROLE", "web") == "mutation_worker":
            raise UnsafeDeploymentProfile(
                "the dedicated mutation worker is only valid in the redis profile"
            )
        return {"profile": profile, "web_workers": workers, "redis_reachable": None}

    if profile != "redis":
        raise UnsafeDeploymentProfile(f"unsupported deployment profile {profile!r}")
    if str(settings.database_url).casefold().startswith("sqlite"):
        raise UnsafeDeploymentProfile(
            "DEPLOYMENT_PROFILE=redis requires a server database such as PostgreSQL; "
            "SQLite cannot share Playarr's process-local writer boundary across workers"
        )
    if not probe_redis(settings.redis_url):
        raise UnsafeDeploymentProfile(
            "DEPLOYMENT_PROFILE=redis requires a reachable REDIS_URL"
        )
    return {"profile": profile, "web_workers": workers, "redis_reachable": True}
