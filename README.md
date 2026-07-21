# typos-config-builder

[![Ask DeepWiki](https://deepwiki.com/badge.svg)](
https://deepwiki.com/leynos/typos-config-builder)

`typos-config-builder` provides one versioned Python 3.14 command-line
interface (CLI) for generating the estate's en-GB-oxendict `typos.toml` files.
It refreshes the shared Oxford dictionary cache when the authority is newer,
merges a repository-local overlay, renders deterministic configuration, and
detects drift in the tracked output.

Until the package has a registry release, consumers should pin an exact Git
commit so that an update is an explicit policy change:

```bash
uvx --from "git+https://github.com/leynos/typos-config-builder.git@FULL_COMMIT_SHA" \
  typos-config-builder --check
```

Replace `FULL_COMMIT_SHA` with the complete commit identifier selected by the
consumer.

The package deliberately does not crawl repositories, harvest spelling data,
run Typos, validate Mermaid diagrams, or act as a general spelling-policy
engine. See the [user guide](docs/users-guide.md) for the consumer contract and
the [design](docs/typos-config-builder-design.md) for the scope boundary.
