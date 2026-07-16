from __future__ import annotations

import re
from collections.abc import Iterable

from market_lens.tools.models import ToolInput, ToolOutput, ToolSpec

TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")


class ToolRegistryError(ValueError):
    pass


class ToolNotFoundError(ToolRegistryError):
    pass


class ToolRegistry:
    def __init__(self, specs: Iterable[ToolSpec] | None = None) -> None:
        self._specs: dict[str, ToolSpec] = {}
        for spec in specs or []:
            self.register(spec)

    def register(self, spec: ToolSpec) -> None:
        if not TOOL_NAME_PATTERN.fullmatch(spec.name):
            raise ToolRegistryError(
                "Tool names must be lowercase, namespaced identifiers such as "
                "'finance.search_assets'"
            )
        if spec.timeout_seconds <= 0:
            raise ToolRegistryError("Tool timeout_seconds must be greater than zero")
        if not issubclass(spec.input_model, ToolInput):
            raise ToolRegistryError("Tool input models must inherit ToolInput")
        if not issubclass(spec.output_model, ToolOutput):
            raise ToolRegistryError("Tool output models must inherit ToolOutput")
        if spec.name in self._specs:
            raise ToolRegistryError(f"Tool is already registered: {spec.name}")
        self._specs[spec.name] = spec

    def get(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Unknown tool: {name}") from exc

    def list(self, capability: str | None = None) -> list[ToolSpec]:
        specs = self._specs.values()
        if capability is not None:
            specs = (spec for spec in specs if spec.capability == capability)
        return sorted(specs, key=lambda spec: spec.name)

    def schemas(self, capability: str | None = None) -> list[dict[str, object]]:
        return [spec.schema() for spec in self.list(capability)]
