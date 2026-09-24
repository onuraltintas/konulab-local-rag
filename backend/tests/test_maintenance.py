from uuid import uuid4

from fastapi.testclient import TestClient

from app.main import app, safe_error_summary


def test_maintenance_mode_blocks_writes_but_can_be_explicitly_closed() -> None:
    with TestClient(app) as client:
        enabled = client.put(
            "/api/admin/maintenance",
            json={"enabled": True, "confirmation": "BAKIM_MODUNU_AC", "reason": "Geri yükleme testi"},
        )
        try:
            blocked = client.post(
                "/api/feedback",
                json={"client_id": str(uuid4()), "kind": "answer_helpful"},
            )
            assert enabled.status_code == 200
            assert enabled.json()["enabled"] is True
            assert blocked.status_code == 503
        finally:
            disabled = client.put(
                "/api/admin/maintenance",
                json={"enabled": False, "confirmation": "BAKIM_MODUNU_KAPAT"},
            )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False


def test_error_summary_redacts_sensitive_values() -> None:
    summary = safe_error_summary(RuntimeError("token=very-secret password: also-secret normal bilgi"))
    assert "very-secret" not in summary
    assert "also-secret" not in summary
    assert "normal bilgi" in summary
