"""Ephemeral, human-reviewed bindings; never infer a target from server cwd."""

import secrets
from dataclasses import dataclass
from pathlib import Path

from releasekit import storage

from .bridge import Bridge, canonical


@dataclass
class ProjectReview:
    bridge: Bridge
    review: dict


class Projects:
    def __init__(self, *, timeout=7200):
        self.timeout = timeout
        self.bindings: dict[str, ProjectReview] = {}

    def inspect(self, root: str) -> ProjectReview:
        if not Path(root).is_absolute():
            raise ValueError("project must be an explicit absolute checkout root")
        root_path = storage.checked(Path(root))
        projection = storage.inside(root_path, root_path / ".github/relkit.pyz")
        if projection.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("projection exceeds the distribution limit")
        bridge = Bridge(root_path, storage.digest(projection), timeout=self.timeout)
        return ProjectReview(bridge, self.review(bridge))

    @staticmethod
    def review(bridge):
        bridge.check()
        return {
            "project": str(bridge.root),
            "projection_sha256": bridge.artifact.sha256,
            "projection_version": bridge.artifact.version,
            "inputs": {
                name: bridge.stamp(bridge.root / name)
                for name in ("relkit.toml", ".betterleaks.toml", "AGENTS.md", ".git/hooks/pre-push")
            },
        }

    def bind(self, prepared: ProjectReview):
        if canonical(self.review(prepared.bridge)) != canonical(prepared.review):
            raise ValueError("project inputs changed after review; inspect and bind again")
        if len(self.bindings) >= 32:
            raise ValueError("binding limit reached; unbind an unused project or restart")
        # Repeated human-approved handles for one snapshot share the operation lock.
        for existing in self.bindings.values():
            if canonical(existing.review) == canonical(prepared.review):
                prepared = existing
                break
        token = secrets.token_hex(16)
        self.bindings[token] = prepared
        return token

    def get(self, token: str):
        bound = self.bindings.get(token)
        if bound is None:
            raise ValueError("unknown or expired binding; call relkit_project bind first")
        if canonical(self.review(bound.bridge)) != canonical(bound.review):
            raise ValueError("reviewed project inputs changed; bind again before another operation")
        return bound.bridge

    def unbind(self, token: str):
        if token not in self.bindings:
            raise ValueError("unknown or expired binding")
        del self.bindings[token]
