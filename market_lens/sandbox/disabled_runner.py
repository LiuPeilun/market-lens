from __future__ import annotations

from market_lens.sandbox.models import SandboxRequest, SandboxResult, SandboxStatus
from market_lens.sandbox.runner import SandboxRunner


class DisabledSandboxRunner(SandboxRunner):
    @property
    def backend_name(self) -> str:
        return "disabled"

    def is_available(self) -> bool:
        return False

    def run(self, request: SandboxRequest) -> SandboxResult:
        del request
        return SandboxResult(
            backend=self.backend_name,
            status=SandboxStatus.UNAVAILABLE,
            error_code="sandbox_disabled",
            message="Sandbox execution is disabled",
        )
