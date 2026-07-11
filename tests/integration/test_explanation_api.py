from fastapi.testclient import TestClient
import pytest

from app.main import app


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as test_client:
        reset_response = test_client.post(
            "/api/v1/replay/reset"
        )

        assert reset_response.status_code == 200

        # 09:00 → 13:50, during the S3 scenario.
        advance_response = test_client.post(
            "/api/v1/replay/advance",
            json={
                "minutes": 290,
            },
        )

        assert advance_response.status_code == 200

        yield test_client


def get_incident(client: TestClient) -> dict:
    response = client.get(
        "/api/v1/incidents"
    )

    assert response.status_code == 200

    incidents = response.json()[
        "incidents"
    ]

    assert incidents

    active = [
        incident
        for incident in incidents
        if incident["status"] == "ACTIVE"
    ]

    return active[0] if active else incidents[0]


def test_explanation_health(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/explanations/health"
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] in {
        "AI_READY",
        "FALLBACK_ONLY",
        "DISABLED",
    }

    assert (
        payload[
            "deterministic_fallback_available"
        ]
        is True
    )


def test_incident_bangla_fallback(
    client: TestClient,
) -> None:
    incident = get_incident(client)

    response = client.post(
        "/api/v1/explanations/incidents/"
        f"{incident['incident_id']}",
        json={
            "language": "bn",
            "audience": "FIELD_OFFICER",
            "viewer_provider_id": None,
            "prefer_ai": False,
        },
    )

    assert response.status_code == 200

    payload = response.json()

    assert (
        payload["generated_by"]
        == "TEMPLATE_FALLBACK"
    )

    assert payload["grounded"] is True
    assert payload["safety_validated"] is True

    assert "পরিস্থিতি" in payload["full_text"]
    assert "অনিশ্চয়তা" in payload["full_text"]


def test_case_explanation_fallback(
    client: TestClient,
) -> None:
    response = client.get(
        "/api/v1/cases/queue"
    )

    assert response.status_code == 200

    cases = response.json()

    assert cases

    case_id = cases[0]["case_id"]

    explanation_response = client.post(
        f"/api/v1/explanations/cases/{case_id}",
        json={
            "language": "banglish",
            "audience": "FIELD_OFFICER",
            "viewer_provider_id": None,
            "prefer_ai": False,
        },
    )

    assert (
        explanation_response.status_code
        == 200
    )

    payload = explanation_response.json()

    assert (
        payload["generated_by"]
        == "TEMPLATE_FALLBACK"
    )

    assert "human review" in (
        payload["full_text"].casefold()
    )


def test_provider_operations_requires_provider(
    client: TestClient,
) -> None:
    incident = get_incident(client)

    response = client.post(
        "/api/v1/explanations/incidents/"
        f"{incident['incident_id']}",
        json={
            "language": "en",
            "audience": "PROVIDER_OPERATIONS",
            "viewer_provider_id": None,
            "prefer_ai": False,
        },
    )

    assert response.status_code == 403