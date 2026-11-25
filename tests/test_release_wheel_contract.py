"""Hold the release workflow to publishing the wheel it builds.

Every release from v0.1.0 to v0.1.4 was published without its wheel. The
upload step searched ``dist/wheels-*``, matched nothing, and still exited
zero, because ``find`` failing on a non-existent start path piped into
``xargs -r`` succeeds. A handout step that cannot fail is worse than none: it
reports success for a release that carries no wheel.

The judgement below is driven over constructed workflows in both directions
before the real file is asserted, because a check over this repository's own
correct workflow passes whether or not it discriminates anything.
"""

import pathlib
import re
import typing as typ

import pytest

from tests.workflow_reading import Document, jobs, load_document, steps

REPOSITORY_ROOT = pathlib.Path(__file__).resolve().parents[1]
RELEASE_WORKFLOW = REPOSITORY_ROOT / ".github" / "workflows" / "release.yml"

#: The job that builds the wheel and the job that publishes the release.
BUILDER: typ.Final = "pure-wheel"
PUBLISHER: typ.Final = "release"

_DOWNLOAD = re.compile(r"^actions/download-artifact@")
_FIND = re.compile(r"\bfind\s+(?P<root>\S+)\s[^|]*-name\s+['\"]?\*\.whl['\"]?")
_GUARD = re.compile(r"\bexit\s+1\b")
_EMPTINESS = re.compile(r"(?:-eq\s+0|==\s*0|-z\b|!\s*-s\b)")


def _executable(script: str) -> str:
    """Return the script's command lines, dropping whole-line comments.

    A commented-out command is not a command. Reading the raw text would let
    `# gh release upload ...` or a trailing `# exit 1` satisfy the contract
    while the workflow publishes nothing.
    """
    return "\n".join(
        line for line in script.splitlines() if not line.lstrip().startswith("#")
    )


def _publisher_steps(document: Document) -> list[dict[object, object]]:
    """Return the release job's steps, or none when the job is absent."""
    return steps(jobs(document).get(PUBLISHER, {}))


def _download_step(document: Document) -> dict[object, object] | None:
    """Return the step that fetches the built wheel, if there is one."""
    return next(
        (
            step
            for step in _publisher_steps(document)
            if _DOWNLOAD.match(str(step.get("uses", "")))
        ),
        None,
    )


def _upload_script(document: Document) -> str | None:
    """Return the executable shell that publishes wheels, if there is any."""
    for step in _publisher_steps(document):
        raw = step.get("run")
        if isinstance(raw, str) and "gh release upload" in (script := _executable(raw)):
            return script
    return None


def _builder_artifact_name(document: Document) -> str | None:
    """Return the artifact name the wheel job hands to the build action."""
    for step in steps(jobs(document).get(BUILDER, {})):
        declared = step.get("with")
        if isinstance(declared, dict) and "artifact-name" in declared:
            return str(declared["artifact-name"])
    return None


def _handout_defects(document: Document) -> list[str]:
    """Return every way this workflow can publish a release without its wheel.

    Each defect names a distinct silent failure: nothing is downloaded, the
    download and the build disagree about the artifact, the upload looks
    somewhere the download never wrote, or an empty search still succeeds.
    """
    download = _download_step(document)
    if download is None:
        return ["the release job downloads no built artifact"]
    declared = download.get("with")
    settings = declared if isinstance(declared, dict) else {}
    named = settings.get("name")
    defects: list[str] = []
    if named is None:
        defects.append("the release download does not name the artifact")
    elif named != _builder_artifact_name(document):
        defects.append(
            f"the release downloads {named!r}, which the wheel job never builds"
        )
    script = _upload_script(document)
    if script is None:
        return [*defects, "the release job never uploads a wheel"]
    found = _FIND.search(script)
    path = settings.get("path")
    if found is None:
        defects.append("the upload never searches for a wheel")
    elif path is not None and found.group("root") != str(path):
        defects.append(
            f"the upload searches {found.group('root')!r}, "
            f"not the download path {path!r}"
        )
    if not (_GUARD.search(script) and _EMPTINESS.search(script)):
        defects.append("an upload that finds no wheel still succeeds")
    return defects


def _guarded_script(search: str) -> str:
    """Return the handout shape this repository must use."""
    return (
        "set -eu\n"
        f"mapfile -d '' wheels < <({search})\n"
        "if (( ${#wheels[@]} == 0 )); then exit 1; fi\n"
        'gh release upload v0.0.0 "${wheels[@]}" --clobber'
    )


def _unguarded_script(search: str) -> str:
    """Return the shape that reports success without publishing a wheel."""
    return f"set -eu\n{search} | xargs -0 gh release upload v0.0.0"


def _workflow(
    *,
    download_name: str | None = "wheels-pure",
    find_root: str = "dist",
    guard: bool = True,
    run_script: str | None = None,
) -> Document:
    """Build a release workflow with one part of the handout varied."""
    declared: dict[object, object] = {"path": "dist"}
    if download_name is not None:
        declared["name"] = download_name
    search = f"find {find_root} -type f -name '*.whl' -print0"
    script = _guarded_script(search) if guard else _unguarded_script(search)
    if run_script is not None:
        script = run_script
    builder = {
        "steps": [
            {
                "uses": "./.github/actions/pure-python-wheel",
                "with": {"python-version": "3.14", "artifact-name": "wheels-pure"},
            }
        ]
    }
    publisher = {
        "steps": [
            {"uses": "actions/download-artifact@v8", "with": declared},
            {"name": "Upload", "run": script},
        ]
    }
    return {"jobs": {BUILDER: builder, PUBLISHER: publisher}}


def test_the_release_workflow_hands_over_the_wheel_it_builds() -> None:
    """The real workflow names, finds, and guards the artifact it publishes."""
    document = load_document(RELEASE_WORKFLOW.read_text(encoding="utf-8"))

    defects = _handout_defects(document)

    assert not defects, defects


@pytest.mark.parametrize(
    ("workflow", "defect"),
    [
        (_workflow(download_name=None), "does not name the artifact"),
        (
            _workflow(download_name="wheels-manylinux"),
            "which the wheel job never builds",
        ),
        (_workflow(find_root="dist/wheels-pure"), "not the download path"),
        (_workflow(guard=False), "still succeeds"),
        (
            _workflow(run_script="set -eu\n# gh release upload v0.0.0 dist/*.whl\n"),
            "never uploads a wheel",
        ),
        (
            _workflow(
                run_script=(
                    "set -eu\n"
                    "find dist -type f -name '*.whl' -print0 | "
                    "xargs -0 gh release upload v0.0.0\n"
                    "# exit 1"
                )
            ),
            "still succeeds",
        ),
    ],
    ids=[
        "unnamed-download",
        "name-mismatch",
        "wrong-root",
        "no-guard",
        "commented-upload",
        "commented-guard",
    ],
)
def test_each_way_of_losing_the_wheel_is_reported(
    workflow: Document, defect: str
) -> None:
    """A workflow that can publish without its wheel is refused, and why."""
    reported = _handout_defects(workflow)

    assert any(defect in message for message in reported), reported
