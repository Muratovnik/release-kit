"""Closed, typed operations; no executable, project or arbitrary argv parameters."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class UserAuthorization(Request):
    """Client attestation of existing user intent, not a native dialog response."""

    source: Literal["user_request"]
    scope: Literal[
        "project_checks",
        "sync_update",
        "sync_rollback",
        "protect_install",
        "release_run",
        "release_resume",
    ]
    review_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


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
    authorization: UserAuthorization | None = None

    @model_validator(mode="after")
    def exact_action(self):
        if self.authorization and (
            self.action != "install" or self.authorization.scope != "protect_install"
        ):
            raise ValueError("authorization is only valid for install with protect_install scope")
        return self


class Release(Request):
    action: Literal["plan", "status", "resume_plan", "run", "resume"]
    version: str = Field(pattern=r"^v?[0-9]+\.[0-9]+\.[0-9]+$")
    plan_hash: str = ""
    no_download: bool = False
    accept_ci_attempt: int = Field(default=0, ge=0)
    authorization: UserAuthorization | None = None

    @model_validator(mode="after")
    def valid_attempt(self):
        if self.accept_ci_attempt and self.action not in ("resume", "resume_plan"):
            raise ValueError("accept_ci_attempt is only valid for resume/resume_plan")
        if self.authorization and self.authorization.scope != {
            "run": "release_run",
            "resume": "release_resume",
        }.get(self.action):
            raise ValueError("authorization must match release run/resume scope")
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
    model_config = ConfigDict(extra="forbid", strict=True)
    approve: bool = Field(description="Approve exactly this operation and its displayed effects")


class Sync(Request):
    root: str
    action: Literal["status", "plan", "apply", "rollback_plan", "rollback"] = "status"
    plan_hash: str = ""
    no_download: bool = False
    authorization: UserAuthorization | None = None

    @model_validator(mode="after")
    def exact_action(self):
        if self.action in ("status", "plan", "rollback_plan") and self.plan_hash:
            raise ValueError("plan_hash is only valid for apply/rollback")
        if self.authorization and self.authorization.scope != {
            "apply": "sync_update",
            "rollback": "sync_rollback",
        }.get(self.action):
            raise ValueError("authorization must match sync apply/rollback scope")
        return self


class Project(Request):
    action: Literal["inspect", "bind", "unbind"] = "inspect"
    root: str = ""
    binding: str = ""
    authorization: UserAuthorization | None = None

    @model_validator(mode="after")
    def exact_target(self):
        if self.action == "unbind":
            if not self.binding or self.root:
                raise ValueError("unbind requires only binding")
        elif not self.root or self.binding:
            raise ValueError("inspect/bind require only an absolute root")
        if self.authorization and (
            self.action != "bind" or self.authorization.scope != "project_checks"
        ):
            raise ValueError("authorization is only valid for bind with project_checks scope")
        return self


class ProjectResponse(BaseModel):
    schema_version: Literal[1] = 1
    adapter_version: str
    action: str
    review: dict | None = None
    binding: str | None = None
    review_sha256: str | None = None
    authorization_source: Literal["user_request", "elicitation"] | None = None
    error: str | None = None
    error_code: str | None = None


class Response(BaseModel):
    schema_version: Literal[1] = 1
    adapter_version: str
    project: str
    projection_sha256: str
    result: dict | None = None
    error: str | None = None
    error_code: str | None = None
    sync: dict | None = None
    review_sha256: str | None = None
    authorization_review: dict | None = None
    authorization_source: Literal["user_request", "elicitation"] | None = None
    diagnostics: str = ""
    diagnostics_truncated: bool = False
    restart_required: bool = False
    retained_scratch: str | None = None
