from __future__ import annotations

from market_lens.config import settings
from market_lens.sandbox.daytona_runner import DaytonaRunner
from market_lens.sandbox.disabled_runner import DisabledSandboxRunner
from market_lens.sandbox.docker_runner import DockerSandboxConfig, DockerSandboxRunner
from market_lens.sandbox.runner import SandboxRunner


def build_sandbox_runner() -> SandboxRunner:
    backend = settings.sandbox_backend.lower()
    if backend == "docker":
        return DockerSandboxRunner(
            DockerSandboxConfig(
                image=settings.docker_sandbox_image,
                temp_root=settings.docker_sandbox_temp_root,
                enabled=True,
            )
        )
    if backend == "daytona":
        return DaytonaRunner()
    return DisabledSandboxRunner()
