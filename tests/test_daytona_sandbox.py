from __future__ import annotations

import base64
import shlex
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pytest

from market_lens.sandbox.daytona_runner import DaytonaRunner, DaytonaSandboxConfig
from market_lens.sandbox.models import (
    SandboxFile,
    SandboxLimits,
    SandboxNetworkPolicy,
    SandboxRequest,
    SandboxStatus,
)


class DaytonaAuthenticationError(Exception):
    pass


class DaytonaTimeoutError(Exception):
    pass


class CleanupError(Exception):
    pass


@dataclass
class FakeResponse:
    exit_code: int = 0


class FakeFileSystem:
    def __init__(self) -> None:
        self.files: dict[str, bytes] = {}
        self.uploads: list[tuple[bytes, str, int]] = []

    def upload_file(self, content: bytes, path: str, timeout: int) -> None:
        self.files[path] = content
        self.uploads.append((content, path, timeout))

    def download_file(self, path: str) -> bytes:
        if path not in self.files:
            raise FileNotFoundError(path)
        return self.files[path]

    def get_file_info(self, path: str) -> object:
        if path not in self.files:
            raise FileNotFoundError(path)
        return SimpleNamespace(size=len(self.files[path]), is_dir=False)


class FakeProcess:
    def __init__(
        self,
        fs: FakeFileSystem,
        *,
        stdout: bytes = b"remote stdout",
        stderr: bytes = b"remote stderr",
        exit_code: int = 0,
        times_out: bool = False,
    ) -> None:
        self.fs = fs
        self.stdout = stdout
        self.stderr = stderr
        self.exit_code = exit_code
        self.times_out = times_out
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def exec(self, command: str, **options: Any) -> FakeResponse:
        self.calls.append((command, options))
        if command.startswith("head -c "):
            parts = shlex.split(command)
            limit = int(parts[2])
            source = parts[3]
            destination = parts[5]
            self.fs.files[destination] = self.fs.files[source][:limit]
            return FakeResponse()
        if options.get("cwd") is None:
            return FakeResponse()
        self.fs.files["market-lens-control/stdout"] = self.stdout
        self.fs.files["market-lens-control/stderr"] = self.stderr
        if self.times_out:
            raise DaytonaTimeoutError("timed out")
        return FakeResponse(exit_code=self.exit_code)


class FakeSandbox:
    def __init__(self, process: FakeProcess, fs: FakeFileSystem) -> None:
        self.process = process
        self.fs = fs


class FakeDaytonaClient:
    def __init__(
        self,
        *,
        stdout: bytes = b"remote stdout",
        stderr: bytes = b"remote stderr",
        exit_code: int = 0,
        times_out: bool = False,
        create_error: Exception | None = None,
        cleanup_error: Exception | None = None,
    ) -> None:
        self.fs = FakeFileSystem()
        self.process = FakeProcess(
            self.fs,
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            times_out=times_out,
        )
        self.sandbox = FakeSandbox(self.process, self.fs)
        self.create_error = create_error
        self.cleanup_error = cleanup_error
        self.create_params: Any | None = None
        self.create_timeout: float | None = None
        self.deleted = False
        self.delete_options: dict[str, Any] = {}

    def create(self, params: Any, timeout: float) -> FakeSandbox:
        if self.create_error:
            raise self.create_error
        self.create_params = params
        self.create_timeout = timeout
        return self.sandbox

    def delete(self, sandbox: FakeSandbox, **options: Any) -> None:
        assert sandbox is self.sandbox
        self.deleted = True
        self.delete_options = options
        if self.cleanup_error:
            raise self.cleanup_error


def make_config(**overrides: Any) -> DaytonaSandboxConfig:
    values: dict[str, Any] = {
        "api_key": "test-key",
        "enabled": True,
        "create_timeout_seconds": 12,
        "delete_timeout_seconds": 8,
    }
    values.update(overrides)
    return DaytonaSandboxConfig(**values)


def make_request(**overrides: Any) -> SandboxRequest:
    values: dict[str, Any] = {
        "command": ["python", "main.py", "value with spaces"],
        "files": [SandboxFile(path="nested/main.py", content="print('ok')")],
        "limits": SandboxLimits(
            timeout_seconds=2,
            cpu_count=1.5,
            memory_mb=1536,
        ),
    }
    values.update(overrides)
    return SandboxRequest(**values)


def test_daytona_runner_requires_configuration() -> None:
    result = DaytonaRunner(client=FakeDaytonaClient()).run(make_request())

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error_code == "daytona_not_configured"


def test_daytona_runner_fails_closed_when_sdk_cannot_initialize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = DaytonaRunner(make_config())

    def fail_to_initialize() -> None:
        raise OSError("local certificate failure with sensitive detail")

    monkeypatch.setattr(runner, "_get_client", fail_to_initialize)

    result = runner.run(make_request())

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error_code == "daytona_sdk_unavailable"
    assert "sensitive detail" not in (result.message or "")


def test_daytona_runner_executes_and_always_deletes_remote_sandbox() -> None:
    client = FakeDaytonaClient()
    client.fs.files["market-lens-output/result.txt"] = b"artifact"
    runner = DaytonaRunner(make_config(), client=client)

    result = runner.run(
        make_request(
            artifact_paths=["result.txt"],
            network_policy=SandboxNetworkPolicy.ALLOWLIST,
            network_allowlist=["api.example.com"],
        )
    )

    assert result.status is SandboxStatus.SUCCESS
    assert result.stdout == "remote stdout"
    assert result.stderr == "remote stderr"
    assert base64.b64decode(result.artifacts[0].content_base64) == b"artifact"
    assert client.create_params.public is False
    assert client.create_params.ephemeral is True
    assert client.create_params.network_block_all is False
    assert client.create_params.domain_allow_list == "api.example.com"
    assert client.create_params.resources.cpu == 2
    assert client.create_params.resources.memory == 2
    assert client.create_timeout == 12
    assert client.fs.uploads[0][1] == "market-lens-workspace/nested/main.py"
    command, options = client.process.calls[-1]
    assert "'value with spaces'" in command
    assert options["cwd"] == "market-lens-workspace"
    assert options["env"]["MARKET_LENS_OUTPUT_DIR"] == "../market-lens-output"
    assert client.deleted is True
    assert client.delete_options == {"timeout": 8, "wait": True}


def test_daytona_runner_blocks_network_by_default() -> None:
    client = FakeDaytonaClient()

    result = DaytonaRunner(make_config(), client=client).run(make_request())

    assert result.status is SandboxStatus.SUCCESS
    assert client.create_params.network_block_all is True
    assert client.create_params.domain_allow_list is None


def test_daytona_runner_can_use_a_controlled_snapshot() -> None:
    client = FakeDaytonaClient()

    result = DaytonaRunner(
        make_config(snapshot="market-lens-python-v1"),
        client=client,
    ).run(make_request())

    assert result.status is SandboxStatus.SUCCESS
    assert client.create_params.snapshot == "market-lens-python-v1"
    assert not hasattr(client.create_params, "resources")


def test_daytona_runner_reports_timeout_and_deletes_sandbox() -> None:
    client = FakeDaytonaClient(times_out=True, stdout=b"partial")

    result = DaytonaRunner(make_config(), client=client).run(make_request())

    assert result.status is SandboxStatus.TIMEOUT
    assert result.error_code == "sandbox_timeout"
    assert result.stdout == "partial"
    assert client.deleted is True


def test_daytona_runner_maps_authentication_failures_without_leaking_details() -> None:
    client = FakeDaytonaClient(
        create_error=DaytonaAuthenticationError("secret provider response")
    )

    result = DaytonaRunner(make_config(), client=client).run(make_request())

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error_code == "daytona_authentication_failed"
    assert "secret provider response" not in (result.message or "")


def test_daytona_runner_fails_closed_when_cleanup_fails() -> None:
    client = FakeDaytonaClient(cleanup_error=CleanupError("delete failed"))

    result = DaytonaRunner(make_config(), client=client).run(make_request())

    assert result.status is SandboxStatus.ERROR
    assert result.error_code == "sandbox_cleanup_failed"
    assert result.stdout == "remote stdout"


def test_daytona_runner_enforces_output_and_artifact_limits() -> None:
    client = FakeDaytonaClient(stdout=b"a" * 800, stderr=b"b" * 800)
    client.fs.files["market-lens-output/large.bin"] = b"x" * 2048
    limits = SandboxLimits(max_output_bytes=1024, max_artifact_bytes=1024)

    result = DaytonaRunner(make_config(), client=client).run(
        make_request(limits=limits, artifact_paths=["large.bin"])
    )

    assert result.status is SandboxStatus.ERROR
    assert result.error_code == "invalid_artifact"
    assert result.output_truncated is True
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == 1024
