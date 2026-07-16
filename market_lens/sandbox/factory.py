from __future__ import annotations

from market_lens.config import settings
from market_lens.sandbox.daytona_runner import DaytonaRunner, DaytonaSandboxConfig
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
        return DaytonaRunner(
            DaytonaSandboxConfig(
                api_key=settings.daytona_api_key,
                api_url=settings.daytona_api_url,
                target=settings.daytona_target,
                image=settings.daytona_sandbox_image,
                snapshot=settings.daytona_snapshot,
                create_timeout_seconds=settings.daytona_create_timeout,
                delete_timeout_seconds=settings.daytona_delete_timeout,
                disk_gb=settings.daytona_disk_gb,
                enabled=True,
            )
        )
    return DisabledSandboxRunner()
