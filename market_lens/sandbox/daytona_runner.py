from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass
from math import ceil
from pathlib import PurePosixPath
from time import monotonic
from typing import Any

from market_lens.sandbox.models import (
    SandboxArtifact,
    SandboxNetworkPolicy,
    SandboxRequest,
    SandboxResult,
    SandboxStatus,
)
from market_lens.sandbox.runner import SandboxRunner

_WORKSPACE_DIR = "market-lens-workspace"
_OUTPUT_DIR = "market-lens-output"
_CONTROL_DIR = "market-lens-control"


@dataclass(frozen=True)
class DaytonaSandboxConfig:
    api_key: str | None = None
    api_url: str | None = None
    target: str | None = None
    image: str = "python:3.11-slim"
    snapshot: str | None = None
    create_timeout_seconds: float = 90
    delete_timeout_seconds: float = 60
    disk_gb: int = 3
    enabled: bool = False


class DaytonaRunner(SandboxRunner):
    def __init__(
        self,
        config: DaytonaSandboxConfig | None = None,
        client: Any | None = None,
    ) -> None:
        self.config = config or DaytonaSandboxConfig()
        self._client = client

    @property
    def backend_name(self) -> str:
        return "daytona"

    def is_available(self) -> bool:
        if not self.config.enabled or not self.config.api_key:
            return False
        try:
            self._get_client()
        except Exception:
            return False
        return True

    def run(self, request: SandboxRequest) -> SandboxResult:
        started = monotonic()
        if not self.config.enabled or not self.config.api_key:
            return self._unavailable(
                started,
                "daytona_not_configured",
                "Daytona sandbox execution is not configured",
            )

        try:
            client = self._get_client()
        except Exception as exc:
            return self._unavailable(
                started,
                "daytona_sdk_unavailable",
                f"Daytona SDK could not be initialized: {type(exc).__name__}",
            )

        sandbox = None
        result: SandboxResult | None = None
        phase = "create"
        try:
            sandbox = client.create(
                _build_create_params(self.config, request),
                timeout=self.config.create_timeout_seconds,
            )
            phase = "prepare"
            _prepare_sandbox(sandbox, request, self.config.create_timeout_seconds)
            phase = "execute"
            result = _execute_request(sandbox, request, started)
        except Exception as exc:
            result = _exception_result(exc, phase, started)

        cleanup_error = None
        if sandbox is not None:
            try:
                client.delete(
                    sandbox,
                    timeout=self.config.delete_timeout_seconds,
                    wait=True,
                )
            except Exception as exc:
                cleanup_error = type(exc).__name__

        if cleanup_error:
            return _cleanup_failed_result(result, cleanup_error, started)
        if result is None:
            return SandboxResult(
                backend="daytona",
                status=SandboxStatus.ERROR,
                error_code="daytona_execution_failed",
                message="Daytona execution did not produce a result",
                duration_ms=_duration_ms(started),
            )
        return result

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client

        from daytona import Daytona, DaytonaConfig

        return Daytona(
            DaytonaConfig(
                api_key=self.config.api_key,
                api_url=self.config.api_url,
                target=self.config.target,
                otel_enabled=False,
            )
        )

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


class DaytonaArtifactError(ValueError):
    pass


def _build_create_params(
    config: DaytonaSandboxConfig,
    request: SandboxRequest,
) -> Any:
    from daytona import (
        CreateSandboxFromImageParams,
        CreateSandboxFromSnapshotParams,
        Resources,
    )

    network_settings = _network_settings(request)
    common: dict[str, Any] = {
        "language": "python",
        "public": False,
        "ephemeral": True,
        "auto_stop_interval": 1,
        "auto_pause_interval": 0,
        "labels": {"market-lens": "sandbox", "managed-by": "market-lens"},
        **network_settings,
    }
    if config.snapshot:
        return CreateSandboxFromSnapshotParams(snapshot=config.snapshot, **common)
    return CreateSandboxFromImageParams(
        image=config.image,
        resources=Resources(
            cpu=max(1, ceil(request.limits.cpu_count)),
            memory=max(1, ceil(request.limits.memory_mb / 1024)),
            disk=max(1, config.disk_gb),
        ),
        **common,
    )


def _network_settings(request: SandboxRequest) -> dict[str, Any]:
    if request.network_policy is SandboxNetworkPolicy.NONE:
        return {
            "network_block_all": True,
            "domain_allow_list": None,
        }
    return {
        "network_block_all": False,
        "domain_allow_list": ",".join(request.network_allowlist),
    }


def _prepare_sandbox(sandbox: Any, request: SandboxRequest, timeout: float) -> None:
    directories = {_WORKSPACE_DIR, _OUTPUT_DIR, _CONTROL_DIR}
    for item in request.files:
        parent = PurePosixPath(item.path).parent
        if parent.as_posix() != ".":
            directories.add(f"{_WORKSPACE_DIR}/{parent.as_posix()}")

    mkdir_command = "mkdir -p " + " ".join(shlex.quote(path) for path in sorted(directories))
    response = sandbox.process.exec(mkdir_command, timeout=max(1, ceil(timeout)))
    if response.exit_code not in {0, None}:
        raise RuntimeError("Daytona workspace initialization failed")

    for item in request.files:
        sandbox.fs.upload_file(
            item.content.encode("utf-8"),
            f"{_WORKSPACE_DIR}/{item.path}",
            timeout=max(1, ceil(timeout)),
        )


def _execute_request(
    sandbox: Any,
    request: SandboxRequest,
    started: float,
) -> SandboxResult:
    stdout_path = f"{_CONTROL_DIR}/stdout"
    stderr_path = f"{_CONTROL_DIR}/stderr"
    command = (
        f"{shlex.join(request.command)} "
        f"> {shlex.quote(f'../{stdout_path}')} "
        f"2> {shlex.quote(f'../{stderr_path}')}"
    )
    try:
        response = sandbox.process.exec(
            command,
            cwd=_WORKSPACE_DIR,
            env={
                "MARKET_LENS_OUTPUT_DIR": f"../{_OUTPUT_DIR}",
                "PYTHONDONTWRITEBYTECODE": "1",
                "PYTHONUNBUFFERED": "1",
            },
            timeout=max(1, ceil(request.limits.timeout_seconds)),
        )
        exit_code = response.exit_code
        timed_out = False
    except Exception as exc:
        if not _is_timeout_error(exc):
            raise
        exit_code = 124
        timed_out = True

    stdout_bytes, stdout_truncated = _download_output_limited(
        sandbox,
        stdout_path,
        request.limits.max_output_bytes,
        "stdout-capped",
    )
    stderr_budget = max(0, request.limits.max_output_bytes - len(stdout_bytes))
    stderr_bytes, stderr_truncated = _download_output_limited(
        sandbox,
        stderr_path,
        stderr_budget,
        "stderr-capped",
    )
    try:
        artifacts = _collect_artifacts(
            sandbox.fs,
            request.artifact_paths,
            request.limits.max_artifact_bytes,
        )
    except DaytonaArtifactError as exc:
        return SandboxResult(
            backend="daytona",
            status=SandboxStatus.ERROR,
            exit_code=exit_code,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            timed_out=timed_out,
            output_truncated=stdout_truncated or stderr_truncated,
            error_code="invalid_artifact",
            message=str(exc),
            duration_ms=_duration_ms(started),
        )

    status = SandboxStatus.TIMEOUT if timed_out else SandboxStatus.SUCCESS
    if not timed_out and exit_code not in {0, None}:
        status = SandboxStatus.ERROR
    return SandboxResult(
        backend="daytona",
        status=status,
        exit_code=exit_code,
        stdout=stdout_bytes.decode("utf-8", errors="replace"),
        stderr=stderr_bytes.decode("utf-8", errors="replace"),
        timed_out=timed_out,
        output_truncated=stdout_truncated or stderr_truncated,
        artifacts=artifacts,
        error_code="sandbox_timeout" if timed_out else None,
        message="Sandbox execution timed out" if timed_out else None,
        duration_ms=_duration_ms(started),
    )


def _download_output_limited(
    sandbox: Any,
    path: str,
    max_bytes: int,
    capped_name: str,
) -> tuple[bytes, bool]:
    try:
        info = sandbox.fs.get_file_info(path)
    except Exception as exc:
        if _is_not_found_error(exc):
            return b"", False
        raise

    size = int(info.size)
    if size <= max_bytes:
        return _download_file(sandbox.fs, path), False
    if max_bytes == 0:
        return b"", True

    capped_path = f"{_CONTROL_DIR}/{capped_name}"
    command = (
        f"head -c {max_bytes} {shlex.quote(path)} "
        f"> {shlex.quote(capped_path)}"
    )
    response = sandbox.process.exec(command)
    if response.exit_code not in {0, None}:
        raise RuntimeError("Daytona output truncation failed")
    content = _download_file(sandbox.fs, capped_path)
    return content[:max_bytes], True


def _collect_artifacts(
    fs: Any,
    artifact_paths: list[str],
    max_bytes: int,
) -> list[SandboxArtifact]:
    total_bytes = 0
    artifacts: list[SandboxArtifact] = []
    for relative_path in artifact_paths:
        remote_path = f"{_OUTPUT_DIR}/{relative_path}"
        try:
            info = fs.get_file_info(remote_path)
        except Exception as exc:
            if _is_not_found_error(exc):
                continue
            raise
        if bool(info.is_dir):
            raise DaytonaArtifactError("Sandbox artifacts must be regular files")
        size = int(info.size)
        if total_bytes + size > max_bytes:
            raise DaytonaArtifactError("Sandbox artifacts exceed the configured size limit")
        content = _download_file(fs, remote_path)
        total_bytes += len(content)
        if total_bytes > max_bytes:
            raise DaytonaArtifactError("Sandbox artifacts exceed the configured size limit")
        artifacts.append(
            SandboxArtifact(
                path=relative_path,
                size_bytes=len(content),
                content_base64=base64.b64encode(content).decode("ascii"),
            )
        )
    return artifacts


def _download_file(fs: Any, path: str) -> bytes:
    content = fs.download_file(path)
    if isinstance(content, str):
        return content.encode("utf-8")
    return bytes(content or b"")


def _exception_result(exc: Exception, phase: str, started: float) -> SandboxResult:
    name = type(exc).__name__
    if _is_timeout_error(exc) and phase == "execute":
        return SandboxResult(
            backend="daytona",
            status=SandboxStatus.TIMEOUT,
            timed_out=True,
            error_code="sandbox_timeout",
            message="Sandbox execution timed out",
            duration_ms=_duration_ms(started),
        )
    if name in {"DaytonaAuthenticationError", "DaytonaAuthorizationError"}:
        return SandboxResult(
            backend="daytona",
            status=SandboxStatus.UNAVAILABLE,
            error_code="daytona_authentication_failed",
            message="Daytona credentials were rejected",
            duration_ms=_duration_ms(started),
        )
    if name == "DaytonaConnectionError":
        return SandboxResult(
            backend="daytona",
            status=SandboxStatus.UNAVAILABLE,
            error_code="daytona_unavailable",
            message="Daytona service is unavailable",
            duration_ms=_duration_ms(started),
        )
    return SandboxResult(
        backend="daytona",
        status=SandboxStatus.ERROR,
        error_code=f"daytona_{phase}_failed",
        message=f"Daytona {phase} failed: {name}",
        duration_ms=_duration_ms(started),
    )


def _cleanup_failed_result(
    result: SandboxResult | None,
    cleanup_error: str,
    started: float,
) -> SandboxResult:
    if result is None:
        return SandboxResult(
            backend="daytona",
            status=SandboxStatus.ERROR,
            error_code="sandbox_cleanup_failed",
            message=f"Remote sandbox cleanup failed: {cleanup_error}",
            duration_ms=_duration_ms(started),
        )
    return result.model_copy(
        update={
            "status": SandboxStatus.ERROR,
            "error_code": "sandbox_cleanup_failed",
            "message": f"Remote sandbox cleanup failed: {cleanup_error}",
            "duration_ms": _duration_ms(started),
        }
    )


def _is_timeout_error(exc: Exception) -> bool:
    return type(exc).__name__ in {"DaytonaTimeoutError", "TimeoutError"}


def _is_not_found_error(exc: Exception) -> bool:
    return type(exc).__name__ in {"DaytonaNotFoundError", "FileNotFoundError"} or getattr(
        exc,
        "status_code",
        None,
    ) == 404


def _duration_ms(started: float) -> int:
    return max(0, int((monotonic() - started) * 1000))
