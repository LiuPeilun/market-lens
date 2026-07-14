from __future__ import annotations

import json
from datetime import date

from market_lens.api.app import to_sse_data


def test_to_sse_data_json_encodes_dates() -> None:
    data = to_sse_data({"type": "meta", "analysis": {"as_of": date(2026, 7, 3)}})

    assert data.startswith("data: ")
    assert data.endswith("\n\n")

    payload = json.loads(data.removeprefix("data: ").strip())
    assert payload == {"type": "meta", "analysis": {"as_of": "2026-07-03"}}
