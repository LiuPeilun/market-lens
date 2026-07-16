from __future__ import annotations

import base64
import json
import os
from pathlib import Path

import pytest

from market_lens.sandbox.docker_runner import (
    DockerSandboxConfig,
    DockerSandboxRunner,
)
from market_lens.sandbox.models import SandboxLimits, SandboxStatus

pytestmark = pytest.mark.skipif(
    os.getenv("MARKET_LENS_RUN_DOCKER_TESTS") != "true",
    reason="set MARKET_LENS_RUN_DOCKER_TESTS=true to run Docker integration tests",
)


def build_runner(tmp_path: Path) -> DockerSandboxRunner:
    return DockerSandboxRunner(
        DockerSandboxConfig(
            image=os.getenv(
                "MARKET_LENS_DOCKER_SANDBOX_IMAGE",
                "python:3.11-slim",
            ),
            temp_root=tmp_path,
            enabled=True,
        )
    )


def test_real_docker_sandbox_blocks_network_and_collects_artifact(
    tmp_path: Path,
) -> None:
    runner = build_runner(tmp_path)
    code = """
from pathlib import Path
import json
import os
import socket

try:
    socket.create_connection(("1.1.1.1", 80), timeout=0.5)
except OSError:
    network = "blocked"
else:
    network = "available"

try:
    Path("/root-write-probe").write_text("unsafe", encoding="utf-8")
except OSError:
    root_filesystem = "readonly"
else:
    root_filesystem = "writable"

print("sandbox stdout")
Path(os.environ["MARKET_LENS_OUTPUT_DIR"]).joinpath("result.txt").write_text(
    json.dumps(
        {
            "network": network,
            "root_filesystem": root_filesystem,
            "uid": os.getuid(),
        }
    ),
    encoding="utf-8",
)
"""

    result = runner.run_python(code, artifact_paths=["result.txt"])

    assert result.status is SandboxStatus.SUCCESS
    assert result.stdout.strip() == "sandbox stdout"
    assert len(result.artifacts) == 1
    artifact_content = base64.b64decode(result.artifacts[0].content_base64)
    assert json.loads(artifact_content) == {
        "network": "blocked",
        "root_filesystem": "readonly",
        "uid": 65534,
    }


def test_real_docker_sandbox_enforces_timeout(tmp_path: Path) -> None:
    runner = build_runner(tmp_path)

    result = runner.run_python(
        "while True:\n    pass\n",
        limits=SandboxLimits(timeout_seconds=0.2),
    )

    assert result.status is SandboxStatus.TIMEOUT
    assert result.timed_out is True
    assert result.error_code == "sandbox_timeout"
