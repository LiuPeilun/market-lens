from __future__ import annotations

from abc import ABC, abstractmethod

from market_lens.sandbox.models import (
    SandboxFile,
    SandboxLimits,
    SandboxRequest,
    SandboxResult,
)


class SandboxRunner(ABC):
    @property
    @abstractmethod
    def backend_name(self) -> str: ...

    @abstractmethod
    def is_available(self) -> bool: ...

    @abstractmethod
    def run(self, request: SandboxRequest) -> SandboxResult: ...

    def run_python(
        self,
        code: str,
        files: list[SandboxFile] | None = None,
        artifact_paths: list[str] | None = None,
        limits: SandboxLimits | None = None,
    ) -> SandboxResult:
        request_files = [SandboxFile(path="main.py", content=code), *(files or [])]
        return self.run(
            SandboxRequest(
                command=["python", "main.py"],
                files=request_files,
                artifact_paths=artifact_paths or [],
                limits=limits or SandboxLimits(),
            )
        )
