"""Opt-in CLI result contract; semantic data never comes from parsing console output."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import __version__


@dataclass
class Result:
    data: dict = field(default_factory=dict)
    errors: list[dict] = field(default_factory=list)
    warnings: list[dict] = field(default_factory=list)
    next_action: list[str] | None = None

    def error(self, code: str, message: object, **location: object) -> None:
        self.errors.append({"code": code, "message": str(message), **location})

    def exposure(self, report: object) -> None:
        self.data["exposure"] = asdict(report)

    def envelope(self, command: list[str], root: str | None, exit_code: int) -> dict:
        if exit_code and not self.errors:
            self.error("command_failed", "Command did not complete successfully; see stderr")
        return {
            "schema_version": 1,
            "tool_version": __version__,
            "command": command,
            "root": root,
            "status": {0: "ok", 1: "failed", 2: "refused", 3: "pending"}[exit_code],
            "exit_code": exit_code,
            "data": self.data,
            "errors": self.errors,
            "warnings": self.warnings,
            "next_action": self.next_action,
        }
