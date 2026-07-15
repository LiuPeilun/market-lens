from __future__ import annotations

import json
from datetime import date

from fastapi.testclient import TestClient

from market_lens.api.app import app, to_sse_data


def test_to_sse_data_json_encodes_dates() -> None:
    data = to_sse_data({"type": "meta", "analysis": {"as_of": date(2026, 7, 3)}})

    assert data.startswith("data: ")
    assert data.endswith("\n\n")

    payload = json.loads(data.removeprefix("data: ").strip())
    assert payload == {"type": "meta", "analysis": {"as_of": "2026-07-03"}}


def test_analyze_requires_authentication() -> None:
    response = TestClient(app).post(
        "/api/analyze",
        json={
            "asset_type": "stock",
            "code": "600519",
            "start": "2024-01-01",
            "end": "2026-07-15",
        },
    )

    assert response.status_code == 401
    assert response.json()["detail"] == "Authentication required"
