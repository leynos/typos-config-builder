"""Shared fixtures for the focused configuration-builder contracts."""

import collections.abc as cabc
from pathlib import Path

import pytest

AuthorityFactory = cabc.Callable[..., Path]


def authority_text(*, stem: str = "organ", accepted: str = "oxendict") -> str:
    """Return the smallest complete shared-authority document."""
    return (
        "schema = 1\n\n[oxford]\n"
        f'stems = ["{stem}"]\n\n'
        f'[words]\naccepted = ["{accepted}"]\n\n'
        "[words.corrections]\n\n"
        "[phrases.corrections]\n\n"
        "[patterns]\nignore = []\n\n"
        '[files]\nexclude = [".git"]\n'
    )


@pytest.fixture
def authority_factory(tmp_path: Path) -> AuthorityFactory:
    """Create complete authority files outside a repository under test."""

    def create(*, stem: str = "organ", accepted: str = "oxendict") -> Path:
        """Write and return one complete authority fixture."""
        authority = tmp_path / f"authority-{stem}.toml"
        authority.write_text(
            authority_text(stem=stem, accepted=accepted),
            encoding="utf-8",
        )
        return authority

    return create


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    """Return an empty repository directory for one builder invocation."""
    path = tmp_path / "repository"
    path.mkdir()
    return path
