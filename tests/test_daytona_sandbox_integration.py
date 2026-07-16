from __future__ import annotations

import base64
import os

import pytest

from market_lens.config import settings
from market_lens.sandbox.daytona_runner import DaytonaRunner, DaytonaSandboxConfig
from market_lens.sandbox.models import SandboxStatus

pytestmark = pytest.mark.skipif(
    os.getenv("MARKET_LENS_RUN_DAYTONA_TESTS") != "true" or not settings.daytona_api_key,
    reason="set MARKET_LENS_RUN_DAYTONA_TESTS=true and DAYTONA_API_KEY to run Daytona tests",
)


def test_real_daytona_sandbox_executes_and_is_deleted() -> None:
    runner = DaytonaRunner(
        DaytonaSandboxConfig(
            api_key=settings.daytona_api_key,
            api_url=settings.daytona_api_url,
            target=settings.daytona_target,
            image=settings.daytona_sandbox_image,
            snapshot=settings.daytona_snapshot,
            enabled=True,
        )
    )
    code = """
import os
from pathlib import Path

print("daytona stdout")
Path(os.environ["MARKET_LENS_OUTPUT_DIR"]).joinpath("result.txt").write_text(
    "remote artifact",
    encoding="utf-8",
)
"""

    result = runner.run_python(code, artifact_paths=["result.txt"])

    assert result.status is SandboxStatus.SUCCESS
    assert result.stdout.strip() == "daytona stdout"
    assert base64.b64decode(result.artifacts[0].content_base64) == b"remote artifact"
