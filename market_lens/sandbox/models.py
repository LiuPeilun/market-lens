from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class SandboxNetworkPolicy(StrEnum):
    NONE = "none"
    ALLOWLIST = "allowlist"


class SandboxStatus(StrEnum):
    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"


class SandboxLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    timeout_seconds: float = Field(default=30, gt=0, le=300)
    memory_mb: int = Field(default=256, ge=64, le=4096)
    cpu_count: float = Field(default=0.5, gt=0, le=4)
    pids_limit: int = Field(default=64, ge=16, le=512)
    max_output_bytes: int = Field(default=1_000_000, ge=1024, le=10_000_000)
    max_artifact_bytes: int = Field(default=5_000_000, ge=1024, le=50_000_000)


class SandboxFile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    content: str = Field(max_length=1_000_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return validate_relative_path(value)


class SandboxArtifact(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    size_bytes: int
    content_base64: str


class SandboxRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    command: list[str] = Field(min_length=1, max_length=64)
    files: list[SandboxFile] = Field(default_factory=list, max_length=64)
    artifact_paths: list[str] = Field(default_factory=list, max_length=32)
    network_policy: SandboxNetworkPolicy = SandboxNetworkPolicy.NONE
    network_allowlist: list[str] = Field(default_factory=list, max_length=64)
    limits: SandboxLimits = Field(default_factory=SandboxLimits)

    @field_validator("command")
    @classmethod
    def validate_command(cls, value: list[str]) -> list[str]:
        if any(not part or len(part) > 512 for part in value):
            raise ValueError("command arguments must be non-empty and at most 512 characters")
        return value

    @field_validator("artifact_paths")
    @classmethod
    def validate_artifact_paths(cls, value: list[str]) -> list[str]:
        return [validate_relative_path(path) for path in value]

    @field_validator("network_allowlist")
    @classmethod
    def validate_network_allowlist(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for domain in value:
            candidate = domain.strip().lower().rstrip(".")
            if not _is_safe_domain(candidate):
                raise ValueError("network allowlist entries must be valid domain names")
            normalized.append(candidate)
        if len(normalized) != len(set(normalized)):
            raise ValueError("network allowlist entries must be unique")
        return normalized

    @model_validator(mode="after")
    def validate_request(self) -> SandboxRequest:
        if self.network_policy is SandboxNetworkPolicy.NONE and self.network_allowlist:
            raise ValueError("network_allowlist requires the allowlist network policy")
        if self.network_policy is SandboxNetworkPolicy.ALLOWLIST and not self.network_allowlist:
            raise ValueError("allowlist network policy requires at least one domain")
        total_input_bytes = sum(len(item.content.encode("utf-8")) for item in self.files)
        if total_input_bytes > 2_000_000:
            raise ValueError("sandbox input files exceed the 2 MB limit")
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("sandbox input file paths must be unique")
        return self


class SandboxResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    backend: str
    status: SandboxStatus
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    output_truncated: bool = False
    artifacts: list[SandboxArtifact] = Field(default_factory=list)
    error_code: str | None = None
    message: str | None = None
    duration_ms: int = 0


def validate_relative_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    path = PurePosixPath(normalized)
    has_windows_drive = bool(path.parts and path.parts[0].endswith(":"))
    if (
        not normalized
        or "\x00" in normalized
        or path.is_absolute()
        or has_windows_drive
        or ".." in path.parts
        or "." in path.parts
    ):
        raise ValueError("sandbox paths must be safe relative paths")
    if any(part in {"", "/"} for part in path.parts):
        raise ValueError("sandbox paths must be safe relative paths")
    return path.as_posix()


_DOMAIN_LABEL = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$")


def _is_safe_domain(value: str) -> bool:
    if not value or len(value) > 253 or "." not in value:
        return False
    return all(_DOMAIN_LABEL.fullmatch(label) for label in value.split("."))
