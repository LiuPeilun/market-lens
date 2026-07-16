from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
from docker.errors import ImageNotFound
from pydantic import ValidationError

from market_lens.sandbox.daytona_runner import DaytonaRunner
from market_lens.sandbox.disabled_runner import DisabledSandboxRunner
from market_lens.sandbox.docker_runner import DockerSandboxConfig, DockerSandboxRunner
from market_lens.sandbox.models import (
    SandboxFile,
    SandboxLimits,
    SandboxNetworkPolicy,
    SandboxRequest,
    SandboxStatus,
)


class FakeImages:
    def __init__(self, image_exists: bool = True) -> None:
        self.image_exists = image_exists

    def get(self, image: str) -> object:
        del image
        if not self.image_exists:
            raise ImageNotFound("missing image")
        return object()


class FakeContainer:
    def __init__(
        self,
        running: bool = False,
        stdout: bytes = b"sandbox stdout",
        stderr: bytes = b"",
        exit_code: int = 0,
    ) -> None:
        self.status = "running" if running else "exited"
        self.stdout = stdout
        self.stderr = stderr
        self.attrs = {"State": {"ExitCode": exit_code}}
        self.killed = False
        self.removed = False

    def reload(self) -> None:
        return None

    def kill(self) -> None:
        self.killed = True
        self.status = "exited"
        self.attrs = {"State": {"ExitCode": 137}}

    def logs(self, stdout: bool, stderr: bool) -> bytes:
        if stdout and not stderr:
            return self.stdout
        if stderr and not stdout:
            return self.stderr
        return b""

    def remove(self, force: bool) -> None:
        assert force is True
        self.removed = True


class FakeContainers:
    def __init__(self, container: FakeContainer, artifact_content: bytes | None = None) -> None:
        self.container = container
        self.artifact_content = artifact_content
        self.last_image: str | None = None
        self.last_options: dict[str, Any] = {}

    def run(self, image: str, **options: Any) -> FakeContainer:
        self.last_image = image
        self.last_options = options
        if self.artifact_content is not None:
            for host_path, mount in options["volumes"].items():
                if mount["bind"] == "/output":
                    artifact_path = Path(host_path) / "result.txt"
                    artifact_path.write_bytes(self.artifact_content)
        return self.container


class FakeDockerClient:
    def __init__(
        self,
        container: FakeContainer | None = None,
        image_exists: bool = True,
        artifact_content: bytes | None = None,
    ) -> None:
        self.images = FakeImages(image_exists=image_exists)
        self.containers = FakeContainers(
            container or FakeContainer(),
            artifact_content=artifact_content,
        )
        self.pinged = False
        self.closed = False

    def ping(self) -> bool:
        self.pinged = True
        return True

    def close(self) -> None:
        self.closed = True


def make_request(**overrides: Any) -> SandboxRequest:
    data: dict[str, Any] = {
        "command": ["python", "/workspace/main.py"],
        "files": [SandboxFile(path="main.py", content="print('ok')")],
        "limits": SandboxLimits(timeout_seconds=1),
    }
    data.update(overrides)
    return SandboxRequest(**data)


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "/etc/passwd",
        "folder/../../secret",
        "C:/Users/secret",
        "C:\\Users\\secret",
        "unsafe\x00name",
        "",
    ],
)
def test_sandbox_rejects_unsafe_paths(path: str) -> None:
    with pytest.raises(ValidationError, match="safe relative paths"):
        SandboxFile(path=path, content="unsafe")


def test_sandbox_rejects_allowlist_without_domains() -> None:
    with pytest.raises(ValidationError, match="at least one domain"):
        make_request(network_policy=SandboxNetworkPolicy.ALLOWLIST)


@pytest.mark.parametrize(
    "domain",
    ["https://example.com", "example.com:443", "*.example.com", "example", "a..com", "bad,com"],
)
def test_sandbox_rejects_unsafe_network_domains(domain: str) -> None:
    with pytest.raises(ValidationError, match="valid domain names"):
        make_request(
            network_policy=SandboxNetworkPolicy.ALLOWLIST,
            network_allowlist=[domain],
        )


def test_disabled_and_daytona_runners_are_unavailable() -> None:
    request = make_request()

    disabled = DisabledSandboxRunner().run(request)
    daytona = DaytonaRunner().run(request)

    assert disabled.status is SandboxStatus.UNAVAILABLE
    assert daytona.status is SandboxStatus.UNAVAILABLE
    assert daytona.error_code == "daytona_not_configured"


def test_docker_runner_requires_explicit_enablement(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(
        DockerSandboxConfig(temp_root=tmp_path, enabled=False),
        client=FakeDockerClient(),
    )

    result = runner.run(make_request())

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error_code == "docker_sandbox_disabled"


def test_docker_runner_does_not_pull_missing_images(tmp_path: Path) -> None:
    runner = DockerSandboxRunner(
        DockerSandboxConfig(image="missing:test", temp_root=tmp_path, enabled=True),
        client=FakeDockerClient(image_exists=False),
    )

    result = runner.run(make_request())

    assert result.status is SandboxStatus.UNAVAILABLE
    assert result.error_code == "sandbox_image_missing"


def test_docker_runner_applies_security_and_resource_limits(tmp_path: Path) -> None:
    client = FakeDockerClient()
    runner = DockerSandboxRunner(
        DockerSandboxConfig(image="python:test", temp_root=tmp_path, enabled=True),
        client=client,
    )

    result = runner.run(make_request())

    options = client.containers.last_options
    assert result.status is SandboxStatus.SUCCESS
    assert result.stdout == "sandbox stdout"
    assert options["network_mode"] == "none"
    assert options["read_only"] is True
    assert options["cap_drop"] == ["ALL"]
    assert options["security_opt"] == ["no-new-privileges:true"]
    assert options["user"] == "65534:65534"
    assert options["mem_limit"] == "256m"
    assert options["nano_cpus"] == 500_000_000
    assert options["pids_limit"] == 64
    assert options["environment"]["MARKET_LENS_OUTPUT_DIR"] == "/output"
    assert options["stdin_open"] is False
    assert options["tty"] is False
    assert {mount["bind"] for mount in options["volumes"].values()} == {
        "/workspace",
        "/output",
    }
    assert client.containers.container.removed is True


def test_docker_runner_enforces_timeout(tmp_path: Path) -> None:
    container = FakeContainer(running=True)
    runner = DockerSandboxRunner(
        DockerSandboxConfig(
            temp_root=tmp_path,
            enabled=True,
            poll_interval_seconds=0.001,
        ),
        client=FakeDockerClient(container=container),
    )

    result = runner.run(
        make_request(limits=SandboxLimits(timeout_seconds=0.01))
    )

    assert result.status is SandboxStatus.TIMEOUT
    assert result.error_code == "sandbox_timeout"
    assert result.exit_code == 137
    assert container.killed is True


def test_docker_runner_limits_combined_output(tmp_path: Path) -> None:
    container = FakeContainer(stdout=b"a" * 800, stderr=b"b" * 800)
    runner = DockerSandboxRunner(
        DockerSandboxConfig(temp_root=tmp_path, enabled=True),
        client=FakeDockerClient(container=container),
    )

    result = runner.run(
        make_request(limits=SandboxLimits(max_output_bytes=1024))
    )

    assert result.output_truncated is True
    assert len(result.stdout.encode()) + len(result.stderr.encode()) == 1024


def test_docker_runner_collects_requested_artifacts(tmp_path: Path) -> None:
    content = b"artifact contents"
    runner = DockerSandboxRunner(
        DockerSandboxConfig(temp_root=tmp_path, enabled=True),
        client=FakeDockerClient(artifact_content=content),
    )

    result = runner.run(make_request(artifact_paths=["result.txt"]))

    assert result.status is SandboxStatus.SUCCESS
    assert result.artifacts[0].path == "result.txt"
    assert base64.b64decode(result.artifacts[0].content_base64) == content


def test_run_python_uses_a_fixed_workspace_script(tmp_path: Path) -> None:
    client = FakeDockerClient()
    runner = DockerSandboxRunner(
        DockerSandboxConfig(temp_root=tmp_path, enabled=True),
        client=client,
    )

    result = runner.run_python("print('hello')")

    assert result.status is SandboxStatus.SUCCESS
    assert client.containers.last_options["command"] == ["python", "main.py"]
