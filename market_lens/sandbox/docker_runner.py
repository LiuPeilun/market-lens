from __future__ import annotations

import base64
import tempfile
from dataclasses import dataclass
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import docker
from docker.errors import DockerException, ImageNotFound

from market_lens.sandbox.models import (
    SandboxArtifact,
    SandboxNetworkPolicy,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)
from market_lens.sandbox.runner import SandboxRunner


@dataclass(frozen=True)
class DockerSandboxConfig:
    image: str = "python:3.11-slim"
    temp_root: Path = Path(".tmp/sandboxes")
    enabled: bool = False
    container_user: str = "65534:65534"
    poll_interval_seconds: float = 0.05


class DockerSandboxRunner(SandboxRunner):
    def __init__(
        self,
        config: DockerSandboxConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or DockerSandboxConfig()
        self._client = client

    @property
    def backend_name(self) -> str:
        return "docker"

    def is_available(self) -> bool:
        if not self.config.enabled:
            return False
        client, owns_client = self._get_client()
        if client is None:
            return False
        try:
            client.ping()
            client.images.get(self.config.image)
            return True
        except (DockerException, OSError):
            return False
        finally:
            if owns_client:
                client.close()

    def run(self, request: SandboxRequest) -> SandboxResult:
        started = monotonic()
        if not self.config.enabled:
            return self._unavailable(
                started,
                "docker_sandbox_disabled",
                "Docker sandbox execution is disabled",
            )
        if request.network_policy is not SandboxNetworkPolicy.NONE:
            return SandboxResult(
                backend=self.backend_name,
                status=SandboxStatus.ERROR,
                error_code="network_policy_unsupported",
                message="The Docker sandbox currently supports only disabled networking",
                duration_ms=_duration_ms(started),
            )

        client, owns_client = self._get_client()
        if client is None:
            return self._unavailable(
                started,
                "docker_unavailable",
                "Docker Desktop is unavailable",
            )
        container = None
        try:
            client.ping()
            try:
                client.images.get(self.config.image)
            except ImageNotFound:
                return self._unavailable(
                    started,
                    "sandbox_image_missing",
                    f"Sandbox image is not available locally: {self.config.image}",
                )

            self.config.temp_root.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(
                prefix="market-lens-",
                dir=self.config.temp_root,
            ) as temp_dir:
                temp_path = Path(temp_dir)
                input_dir = temp_path / "input"
                output_dir = temp_path / "output"
                input_dir.mkdir()
                output_dir.mkdir()
                _write_input_files(input_dir, request)

                container = client.containers.run(
                    self.config.image,
                    command=request.command,
                    detach=True,
                    network_mode="none",
                    read_only=True,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    user=self.config.container_user,
                    working_dir="/workspace",
                    volumes={
                        str(input_dir.resolve()): {"bind": "/workspace", "mode": "ro"},
                        str(output_dir.resolve()): {"bind": "/output", "mode": "rw"},
                    },
                    tmpfs={"/tmp": "rw,nosuid,nodev,noexec,size=64m"},
                    mem_limit=f"{request.limits.memory_mb}m",
                    nano_cpus=int(request.limits.cpu_count * 1_000_000_000),
                    pids_limit=request.limits.pids_limit,
                    environment={
                        "HOME": "/tmp",
                        "PYTHONDONTWRITEBYTECODE": "1",
                        "PYTHONUNBUFFERED": "1",
                    },
                    labels={
                        "com.market-lens.sandbox": "true",
                        "com.market-lens.backend": self.backend_name,
                    },
                    init=True,
                    stdin_open=False,
                    tty=False,
                )
                timed_out = _wait_for_container(
                    container,
                    timeout_seconds=request.limits.timeout_seconds,
                    poll_interval_seconds=self.config.poll_interval_seconds,
                )
                container.reload()
                state = container.attrs.get("State") or {}
                exit_code = state.get("ExitCode")
                stdout_bytes = container.logs(stdout=True, stderr=False) or b""
                stderr_bytes = container.logs(stdout=False, stderr=True) or b""
                stdout, stderr, truncated = _decode_output(
                    stdout_bytes,
                    stderr_bytes,
                    request.limits.max_output_bytes,
                )
                try:
                    artifacts = _collect_artifacts(
                        output_dir,
                        request.artifact_paths,
                        request.limits.max_artifact_bytes,
                    )
                except SandboxArtifactError as exc:
                    return SandboxResult(
                        backend=self.backend_name,
                        status=SandboxStatus.ERROR,
                        exit_code=exit_code,
                        stdout=stdout,
                        stderr=stderr,
                        timed_out=timed_out,
                        output_truncated=truncated,
                        error_code="invalid_artifact",
                        message=str(exc),
                        duration_ms=_duration_ms(started),
                    )

                status = SandboxStatus.TIMEOUT if timed_out else SandboxStatus.SUCCESS
                if not timed_out and exit_code not in {0, None}:
                    status = SandboxStatus.ERROR
                return SandboxResult(
                    backend=self.backend_name,
                    status=status,
                    exit_code=exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    timed_out=timed_out,
                    output_truncated=truncated,
                    artifacts=artifacts,
                    error_code="sandbox_timeout" if timed_out else None,
                    message="Sandbox execution timed out" if timed_out else None,
                    duration_ms=_duration_ms(started),
                )
        except (DockerException, OSError) as exc:
            return SandboxResult(
                backend=self.backend_name,
                status=SandboxStatus.ERROR,
                error_code="docker_execution_failed",
                message=f"Docker sandbox execution failed: {type(exc).__name__}",
                duration_ms=_duration_ms(started),
            )
        finally:
            if container is not None:
                try:
                    container.remove(force=True)
                except DockerException:
                    pass
            if owns_client:
                client.close()

    def _get_client(self) -> tuple[Any | None, bool]:
        if self._client is not None:
            return self._client, False
        try:
            return docker.from_env(), True
        except DockerException:
            return None, False

    def _unavailable(
        self,
        started: float,
        error_code: str,
        message: str,
    ) -> SandboxResult:
        return SandboxResult(
            backend=self.backend_name,
            status=SandboxStatus.UNAVAILABLE,
            error_code=error_code,
            message=message,
            duration_ms=_duration_ms(started),
        )


class SandboxArtifactError(ValueError):
    pass


def _write_input_files(input_dir: Path, request: SandboxRequest) -> None:
    root = input_dir.resolve()
    for item in request.files:
        target = input_dir / item.path
        target.parent.mkdir(parents=True, exist_ok=True)
        resolved = target.resolve()
        if not resolved.is_relative_to(root):
            raise OSError("Sandbox input path escaped its workspace")
        target.write_text(item.content, encoding="utf-8")


def _wait_for_container(
    container: Any,
    timeout_seconds: float,
    poll_interval_seconds: float,
) -> bool:
    deadline = monotonic() + timeout_seconds
    while monotonic() < deadline:
        container.reload()
        if container.status not in {"created", "running", "restarting"}:
            return False
        sleep(poll_interval_seconds)
    container.kill()
    return True


def _decode_output(
    stdout_bytes: bytes,
    stderr_bytes: bytes,
    max_bytes: int,
) -> tuple[str, str, bool]:
    truncated = len(stdout_bytes) + len(stderr_bytes) > max_bytes
    stdout_limited = stdout_bytes[:max_bytes]
    remaining = max(0, max_bytes - len(stdout_limited))
    stderr_limited = stderr_bytes[:remaining]
    return (
        stdout_limited.decode("utf-8", errors="replace"),
        stderr_limited.decode("utf-8", errors="replace"),
        truncated,
    )


def _collect_artifacts(
    output_dir: Path,
    artifact_paths: list[str],
    max_bytes: int,
) -> list[SandboxArtifact]:
    root = output_dir.resolve()
    total_bytes = 0
    artifacts: list[SandboxArtifact] = []
    for relative_path in artifact_paths:
        candidate = output_dir / relative_path
        if not candidate.exists():
            continue
        if candidate.is_symlink() or not candidate.is_file():
            raise SandboxArtifactError("Sandbox artifacts must be regular files")
        resolved = candidate.resolve()
        if not resolved.is_relative_to(root):
            raise SandboxArtifactError("Sandbox artifact escaped its output directory")
        content = candidate.read_bytes()
        total_bytes += len(content)
        if total_bytes > max_bytes:
            raise SandboxArtifactError("Sandbox artifacts exceed the configured size limit")
        artifacts.append(
            SandboxArtifact(
                path=relative_path,
                size_bytes=len(content),
                content_base64=base64.b64encode(content).decode("ascii"),
            )
        )
    return artifacts


def _duration_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
