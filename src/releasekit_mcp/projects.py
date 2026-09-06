"""Ephemeral, human-reviewed bindings; never infer a target from server cwd."""

import secrets
from dataclasses import dataclass
from pathlib import Path

from releasekit import config, distribution, storage

from .bridge import Bridge, canonical


@dataclass
class ProjectReview:
    bridge: Bridge
    review: dict


class Projects:
    def __init__(self, *, timeout=7200):
        self.timeout = timeout
        self.bindings: dict[str, ProjectReview] = {}

    def inspect(self, root: str, *, bundle=None) -> ProjectReview:
        if not Path(root).is_absolute():
            raise ValueError("project must be an explicit absolute checkout root")
        root_path = storage.checked(Path(root))
        projection = storage.inside(root_path, root_path / ".github/relkit.pyz")
        if projection.stat().st_size > distribution.MAX_ARCHIVE_BYTES:
            raise ValueError("projection exceeds the distribution limit")
        bridge = Bridge(root_path, storage.digest(projection), timeout=self.timeout, bundle=bundle)
        return ProjectReview(bridge, self.review(bridge))

    @staticmethod
    def review(bridge):
        bridge.check()
        policy_stamp = bridge.stamp(bridge.root / "relkit.toml")
        try:
            policy = config.load(bridge.root, required=False)
        except config.ConfigError as error:
            raise ValueError(str(error)) from error
        names = [
            "relkit.toml",
            policy.exposure.betterleaks_config,
            "AGENTS.md",
            ".git/hooks/pre-push",
        ]
        if policy.release is not None:
            # The workflow is what publishes, and the owner guard pins it. A change
            # to it must expire this binding exactly like a change to the policy.
            names.append(policy.release.workflow)
        inputs = {name: bridge.stamp(bridge.root / name) for name in dict.fromkeys(names)}
        if inputs["relkit.toml"] != policy_stamp:
            raise ValueError("project inputs changed during review; inspect again")
        return {
            "project": str(bridge.root),
            "projection_sha256": bridge.artifact.sha256,
            "projection_version": bridge.artifact.version,
            "inputs": inputs,
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
