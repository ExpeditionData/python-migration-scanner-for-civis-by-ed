"""Every README section and step that the app cites exists (docs/PLAN.md 17.14, Part C).

The Setup page's fix texts (app/setup.py) and other app text point admins to README sections and step numbers,
for example README "Install", step 8. This test reads every string in app/ (Python string literals, with adjacent
literals joined the way Python joins them, and the templates) and fails if a cited section is not a README heading
or a cited step is not a numbered step in that section.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
CITATION = re.compile(r'README,? "(?P<section>[^"]+)"(?:,? (?:step (?P<one>\d+)|steps (?P<many>\d+(?:(?:,| and|, and) \d+)+)))?')
# A citation written without quotes around the section, for example: README, Create the scanner job.
UNQUOTED = re.compile(r'README,? (?!")[A-Z][a-z]')


def readme_steps() -> dict[str, set[int]]:
    """Each README heading's title, with the numbers of the numbered steps directly under it."""
    sections: dict[str, set[int]] = {}
    current = None
    for line in (ROOT / "README.md").read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^#{1,6} (.+?)\s*$", line)
        if heading:
            current = heading.group(1)
            sections.setdefault(current, set())
            continue
        step = re.match(r"^(\d+)\. ", line)
        if step and current is not None:
            sections[current].add(int(step.group(1)))
    return sections


def app_texts() -> list[tuple[str, str]]:
    texts = []
    for path in sorted((ROOT / "app").rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                texts.append((rel, node.value))
    for path in sorted((ROOT / "app" / "templates").rglob("*.html")):
        texts.append((path.relative_to(ROOT).as_posix(), path.read_text(encoding="utf-8")))
    return texts


def citations() -> list[tuple[str, str, tuple[int, ...]]]:
    found = []
    for rel, text in app_texts():
        for match in CITATION.finditer(text):
            steps = match.group("one") or match.group("many") or ""
            found.append((rel, match.group("section"), tuple(int(n) for n in re.findall(r"\d+", steps))))
    return found


def test_every_readme_section_and_step_the_app_cites_exists():
    sections = readme_steps()
    cited = citations()
    assert cited, "no README citation found in app/"
    missing = []
    for rel, section, steps in cited:
        if section not in sections:
            missing.append(f"{rel}: section {section!r}")
            continue
        missing += [f"{rel}: {section!r} step {n}" for n in steps if n not in sections[section]]
    assert missing == []


def test_every_readme_citation_in_the_app_quotes_its_section():
    unquoted = [f"{rel}: {match.group(0)}" for rel, text in app_texts() for match in UNQUOTED.finditer(text)]
    assert unquoted == []


def test_the_setup_page_cites_the_new_sections():
    sections = {section for _, section, _ in citations()}
    assert {"Install", "What the install script sets", "Update to a new release", "Before you start"} <= sections
    assert not sections & {"Create the scanner job", "Create the Service", "Settings the Civis forms cannot set"}


def test_the_checker_finds_a_missing_step():
    """The parser itself: a step that is not there is reported."""
    sections = readme_steps()
    assert 8 in sections["Install"] and 10 in sections["Install"] and 99 not in sections["Install"]
    match = CITATION.search('see README "Install", steps 8 and 10')
    assert match and re.findall(r"\d+", match.group("many")) == ["8", "10"]
