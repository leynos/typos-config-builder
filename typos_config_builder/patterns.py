"""Validate bounded regular expressions and local file exclusions."""

from __future__ import annotations

import dataclasses as dc
import pathlib
import re

GENERIC_PROSE = ("ordinary prose", "unrelated_identifier")
UNIVERSAL_FILE_GLOBS = frozenset({
    "*",
    "**",
    "**/*",
    "**/**",
    "**/*.*",
    "*.md",
    "**.md",
    "**/*.md",
})
BACKREFERENCE = re.compile(r"\\(?:[1-9]|g<|k<)|\(\?P=")
REPETITION = re.compile(r"\{(?:\d+(?:,\d*)?|,\d+)\}")


@dc.dataclass(slots=True)
class _GroupState:
    """Track ambiguity and adjacent quantified atoms within one regex group."""

    has_repetition: bool = False
    has_alternation: bool = False
    atoms_since_repetition: int | None = None

    def note_atom(self) -> None:
        """Record one atom separating this group's direct repetitions."""
        if self.atoms_since_repetition is not None:
            self.atoms_since_repetition += 1

    def note_repetition(self, *, repeats_ambiguous_group: bool) -> bool:
        """Record a repetition and report whether it compounds ambiguity."""
        is_unsafe = self.atoms_since_repetition == 1 or repeats_ambiguous_group
        self.has_repetition = True
        self.atoms_since_repetition = 0
        return is_unsafe


@dc.dataclass(slots=True)
class _RepetitionScanner:
    """Recognize unsafe nested or adjacent repetition in one regex pattern."""

    pattern: str
    groups: list[_GroupState] = dc.field(
        default_factory=lambda: [_GroupState()],
        init=False,
    )
    position: int = 0
    is_in_character_class: bool = False
    previous_group_is_ambiguous: bool = False

    def has_unsafe_repetition(self) -> bool:
        """Report whether scanning finds repetition that compounds ambiguity."""
        while self.position < len(self.pattern):
            if self._consume_current_character():
                return True
        return False

    def _consume_current_character(self) -> bool:
        """Consume one regex token and report an unsafe repetition suffix."""
        character = self.pattern[self.position]
        if self.is_in_character_class:
            self._consume_character_class(character)
            return False
        match character:
            case "\\":
                self._consume_escape()
            case "[":
                self._open_character_class()
            case "(":
                self._open_group()
            case ")" if len(self.groups) > 1:
                self._close_group()
            case "|":
                self._consume_alternation()
            case _:
                return self._consume_atom_or_operator(character)
        return False

    def _consume_character_class(self, character: str) -> None:
        """Advance through a character class without parsing its contents."""
        if character == "\\":
            self.position += 2
            return
        self.is_in_character_class = character != "]"
        self.position += 1

    def _consume_escape(self) -> None:
        """Treat an escaped token as one atom and skip its escaped character."""
        self.groups[-1].note_atom()
        self.previous_group_is_ambiguous = False
        self.position += 2

    def _open_character_class(self) -> None:
        """Record a character class as one atom and enter its contents."""
        self.is_in_character_class = True
        self.groups[-1].note_atom()
        self.previous_group_is_ambiguous = False
        self.position += 1

    def _open_group(self) -> None:
        """Begin tracking ambiguity within a nested group."""
        self.groups.append(_GroupState())
        self.previous_group_is_ambiguous = False
        self.position += 1

    def _close_group(self) -> None:
        """Merge a completed group's ambiguity into its parent group."""
        closed_group = self.groups.pop()
        parent_group = self.groups[-1]
        parent_group.note_atom()
        parent_group.has_repetition |= closed_group.has_repetition
        parent_group.has_alternation |= closed_group.has_alternation
        self.previous_group_is_ambiguous = (
            closed_group.has_repetition or closed_group.has_alternation
        )
        self.position += 1

    def _consume_alternation(self) -> None:
        """Mark the current group as ambiguous across its alternatives."""
        self.groups[-1].has_alternation = True
        self.groups[-1].atoms_since_repetition = None
        self.previous_group_is_ambiguous = False
        self.position += 1

    def _consume_atom_or_operator(self, character: str) -> bool:
        """Consume a plain atom or repetition-related regex operator."""
        repetition = REPETITION.match(self.pattern, self.position)
        if self._is_group_syntax(character) or self._is_repetition_modifier(character):
            self._advance_one_character()
            return False
        if character not in "*+?" and repetition is None:
            self.groups[-1].note_atom()
            self._advance_one_character()
            return False
        is_unsafe = self.groups[-1].note_repetition(
            repeats_ambiguous_group=self.previous_group_is_ambiguous,
        )
        self.previous_group_is_ambiguous = False
        self.position = self.position + 1 if repetition is None else repetition.end()
        return is_unsafe

    def _is_group_syntax(self, character: str) -> bool:
        """Report whether a question mark introduces special group syntax."""
        previous = self.pattern[self.position - 1 : self.position]
        return character == "?" and previous == "("

    def _is_repetition_modifier(self, character: str) -> bool:
        """Report whether a suffix modifies an existing repetition operator."""
        previous = self.pattern[self.position - 1 : self.position]
        return character in "+?" and previous in "*+?}"

    def _advance_one_character(self) -> None:
        """Advance past a token that breaks adjacency with a closed group."""
        self.previous_group_is_ambiguous = False
        self.position += 1


def compile_pattern(pattern: str) -> re.Pattern[str]:
    """Compile a policy regex after rejecting backtracking-prone forms."""
    try:
        compiled = re.compile(pattern)
    except re.error as error:
        message = f"ignore pattern is invalid: {pattern!r} ({error})"
        raise ValueError(message) from error
    if (
        BACKREFERENCE.search(pattern)
        or _RepetitionScanner(pattern).has_unsafe_repetition()
    ):
        message = f"ignore pattern has unsafe repetition: {pattern!r}"
        raise ValueError(message)
    return compiled


def validate_local_exceptions(
    ignore_patterns: tuple[str, ...], excluded_files: tuple[str, ...]
) -> None:
    """Reject local regexes or globs that weaken shared spelling policy."""
    for pattern in ignore_patterns:
        compiled = compile_pattern(pattern)
        if compiled.search("") is not None or any(
            compiled.search(probe) for probe in GENERIC_PROSE
        ):
            message = f"local ignore pattern is too broad: {pattern!r}"
            raise ValueError(message)
    for pattern in excluded_files:
        normalized = pathlib.Path(pattern.strip()).as_posix().casefold()
        if normalized in UNIVERSAL_FILE_GLOBS:
            message = f"local file exclusion is too broad: {pattern!r}"
            raise ValueError(message)
