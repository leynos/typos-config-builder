# Documentation contents

[Documentation contents](contents.md) is the index for typos-config-builder's
documentation set.

## Project guides

- [User guide](users-guide.md) explains exact-version invocation, consumer
  files, cache refresh, generation, and drift checking.
- [Developer guide](developers-guide.md) explains the implementation contract,
  quality baseline, and change discipline.
- [Repository layout](repository-layout.md) maps the package, tests,
  documentation, and consumer-owned files.
- [Documentation style guide](documentation-style-guide.md) defines the
  spelling, structure, Markdown, Architecture Decision Record (ADR), Request
  for Comments (RFC), and roadmap conventions used by this documentation set.

## Design and decisions

- [typos-config-builder design](typos-config-builder-design.md) defines the
  deterministic generation pipeline and its deliberate boundaries.
- [ADR 0001: Keep the builder focused](adrs/0001-keep-the-builder-focused.md)
  records why rollout, harvesting, and tool orchestration remain out of scope.

## Engineering practice

- [Complexity antipatterns and refactoring strategies](complexity-antipatterns-and-refactoring-strategies.md)
  explains cognitive complexity, the bumpy-road antipattern, and refactoring
  approaches for maintainable code.
- [Local validation of GitHub Actions with act and pytest](local-validation-of-github-actions-with-act-and-pytest.md)
  explains how to validate workflow behaviour locally before relying on remote
  Continuous Integration (CI) runs.
- [Scripting standards](scripting-standards.md) explains the preferred Python
  scripting stack, command execution patterns, and test expectations for helper
  scripts.
