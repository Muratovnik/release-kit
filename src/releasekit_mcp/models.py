"""Closed, typed operations; no executable, project or arbitrary argv parameters."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Version(Request):
    pass


class Exposure(Request):
    owner: bool = False
    strict: bool = False


class Audit(Exposure):
    scope: Literal["worktree", "staged", "history"] = "worktree"
    require_overlay: bool = False
    no_download: bool = False


class Overlay(Request):
    pass


class Notes(Request):
    version: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,127}$")
    changelog: str = "CHANGELOG.md"
    strict: bool = False
    output: str | None = None


class Protect(Request):
    action: Literal["check", "plan", "install"] = "check"
    plan_hash: str = ""


class Release(Request):
    action: Literal["plan", "status", "run", "resume"]
    version: str = Field(pattern=r"^v?[0-9]+\.[0-9]+\.[0-9]+$")
    plan_hash: str = ""
    no_download: bool = False
    accept_ci_attempt: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def valid_attempt(self):
        if self.accept_ci_attempt and self.action != "resume":
            raise ValueError("accept_ci_attempt is only valid for resume")
        return self


class Update(Request):
    action: Literal["plan", "apply", "rollback_plan", "rollback"] = "plan"
    artifact: str | None = None
    sha256: str = ""
    repository: str = ""
    release: str = ""
    refresh_guard: bool = False
    plan_hash: str = ""
    no_download: bool = False


class Confirmation(BaseModel):
    approve: bool = Field(description="Approve exactly this operation and its displayed effects")


class Project(Request):
    action: Literal["inspect", "bind", "unbind"] = "inspect"
    root: str = ""
    binding: str = ""

    @model_validator(mode="after")
    def exact_target(self):
        if self.action == "unbind":
            if not self.binding or self.root:
                raise ValueError("unbind requires only binding")
        elif not self.root or self.binding:
            raise ValueError("inspect/bind require only an absolute root")
        return self


class ProjectResponse(BaseModel):
    schema_version: Literal[1] = 1
    adapter_version: str
    action: str
    review: dict | None = None
    binding: str | None = None


class Response(BaseModel):
    schema_version: Literal[1] = 1
    adapter_version: str
    project: str
    projection_sha256: str
    result: dict | None = None
    error: str | None = None
    diagnostics: str = ""
    diagnostics_truncated: bool = False
    restart_required: bool = False
    retained_scratch: str | None = None
