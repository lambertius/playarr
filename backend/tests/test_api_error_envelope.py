from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import BaseModel

from app.services.api_errors import install_structured_error_handlers
from app.services.file_operations import FilePlanCollision, FileWaitingForRelease
from app.services.mutation_coordinator import StaleRevisionError


class Payload(BaseModel):
    count: int


def _client():
    app = FastAPI()
    install_structured_error_handlers(app)

    @app.get("/conflict")
    def conflict():
        raise HTTPException(409, {
            "code": "stale_revision", "message": "Changed elsewhere",
            "operation_id": "op-1", "retryable": False,
        })

    @app.post("/validate")
    def validate(payload: Payload):
        return payload

    @app.get("/stale")
    def stale():
        raise StaleRevisionError(2, 3)

    @app.get("/file-collision")
    def file_collision():
        raise FilePlanCollision([{"destination": "library/Track.mp4"}])

    @app.get("/file-locked")
    def file_locked():
        raise FileWaitingForRelease("media is open by another process")

    @app.get("/provider-timeout")
    def provider_timeout():
        raise TimeoutError("provider did not answer")

    @app.get("/invalid-policy")
    def invalid_policy():
        raise HTTPException(400, {
            "code": "invalid_import_policy", "message": "Modes are mutually exclusive",
            "field_errors": {"metadata_mode": ["choose one mode"]},
        })

    return TestClient(app, raise_server_exceptions=False)


def test_structured_http_error_preserves_domain_code_and_operation():
    response = _client().get("/conflict")
    assert response.status_code == 409
    assert response.json() == {
        "code": "stale_revision",
        "message": "Changed elsewhere",
        "operation_id": "op-1",
        "retryable": False,
        "field_errors": {},
        "diagnostics_id": None,
        "request_id": None,
    }


def test_validation_error_uses_field_error_map():
    response = _client().post("/validate", json={"count": "not-a-number"})
    payload = response.json()
    assert response.status_code == 422
    assert payload["code"] == "validation_error"
    assert payload["message"] == "Request validation failed"
    assert "count" in payload["field_errors"]
    assert set(payload) >= {
        "code", "message", "operation_id", "retryable",
        "field_errors", "diagnostics_id",
    }


def test_domain_failures_are_distinguishable_without_server_logs():
    client = _client()
    cases = (
        ("/stale", 409, "stale_revision", False),
        ("/file-collision", 409, "file_collision", False),
        ("/file-locked", 423, "file_locked", True),
        ("/provider-timeout", 503, "provider_timeout", True),
        ("/invalid-policy", 400, "invalid_import_policy", False),
    )
    for path, status, code, retryable in cases:
        response = client.get(path)
        payload = response.json()
        assert response.status_code == status, path
        assert payload["code"] == code, path
        assert payload["retryable"] is retryable, path
        assert payload["message"], path
        assert set(payload) >= {
            "operation_id", "field_errors", "diagnostics_id", "request_id",
        }
    assert client.get("/stale").json()["current_revision"] == 3
    assert client.get("/file-collision").json()["collisions"]
    assert "metadata_mode" in client.get("/invalid-policy").json()["field_errors"]
