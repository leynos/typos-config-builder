"""Build deterministic en-GB-oxendict configuration for ``typos``."""

from typos_config_builder.builder import (
    BuildResult,
    ConfigBuilderError,
    ConfigDriftError,
    build,
)

__all__ = ["BuildResult", "ConfigBuilderError", "ConfigDriftError", "build"]
