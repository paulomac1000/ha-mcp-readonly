#!/usr/bin/env python3
"""Validate governed Markdown documents used by this skills collection."""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import unquote

import yaml

FRONTMATTER = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.S)
HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.M)
FENCE_OPENER = re.compile(r"^(?P<indent>[ \t]*)(?P<fence>`{3,}|~{3,})(?P<info>[^\r\n]*)$")
REFERENCE_DEFINITION = re.compile(
    r"^(?P<indent>[ ]{0,3})\[(?P<label>[^\]\n]+)\]:[ \t]*(?P<raw>[^\r\n]+)$",
    re.M,
)
REFERENCE_LINK = re.compile(r"(?<!\!)\[(?P<label>(?:\\.|[^\]\n])+)\]\[(?P<reference>[^\]\n]*)\]")
BRACKETED_LABEL = re.compile(r"\[(?P<label>(?:\\.|[^\]\n])+)\]")
DOC_ID = re.compile(r"^(workflow|reference|system|guide|decision|contract)\.[a-z0-9][a-z0-9.-]*$")
REQUIRED = {"description", "doc_id", "type", "status", "rigor", "owners"}
VALID_TYPES = {"workflow", "reference", "system", "guide", "decision", "contract"}
VALID_STATUS = {"draft", "active", "evolving", "deprecated", "archived"}
VALID_RIGOR = {"informative", "operational", "normative"}
AUTOMATION_FIELDS = {
    "last_verified",
    "fitness_score",
    "semantic_hash",
    "dependency_versions",
    "backlinks",
}
EXEMPT_NAMES = {"README.md", "SKILL.md", "CHANGELOG.md"}


@dataclass(frozen=True)
class Finding:
    """One validation error associated with a path."""

    path: Path
    message: str


def collect_files(inputs: Iterable[Path]) -> tuple[list[Path], list[Finding]]:
    """Resolve explicit Markdown inputs and reject missing or unsupported paths."""
    files: set[Path] = set()
    findings: list[Finding] = []
    for item in inputs:
        if not item.exists():
            findings.append(Finding(item, "input does not exist"))
        elif item.is_file():
            if item.suffix.lower() != ".md":
                findings.append(Finding(item, "explicit input is not a Markdown file"))
            else:
                files.add(item)
        elif item.is_dir():
            files.update(
                path
                for path in item.rglob("*.md")
                if not {".git", ".venv", "__pycache__"}.intersection(path.parts)
            )
        else:
            findings.append(Finding(item, "unsupported input type"))
    if not files and not findings:
        findings.append(Finding(Path("."), "no Markdown documents selected"))
    return sorted(files), findings


def _blank_line(line: str) -> str:
    """Replace one source line with the same line ending and no content."""
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n") or line.endswith("\r"):
        return line[-1]
    return ""


def _indentation_columns(prefix: str) -> int:
    """Count leading Markdown indentation using four-column tab stops."""
    columns = 0
    for character in prefix:
        columns += 4 - (columns % 4) if character == "\t" else 1
    return columns


def strip_fenced_blocks(text: str) -> str:
    """Remove CommonMark fenced blocks while preserving source line boundaries."""
    output: list[str] = []
    fence_character: str | None = None
    minimum_length = 0
    for line in text.splitlines(keepends=True):
        candidate = line.rstrip("\r\n")
        if fence_character is None:
            opener = FENCE_OPENER.match(candidate)
            if opener and _indentation_columns(opener.group("indent")) <= 3:
                marker = opener.group("fence")
                info = opener.group("info")
                if marker[0] != "`" or "`" not in info:
                    fence_character = marker[0]
                    minimum_length = len(marker)
                    output.append(_blank_line(line))
                    continue
            output.append(line)
            continue

        stripped = candidate.lstrip(" \t")
        indentation_prefix = candidate[: len(candidate) - len(stripped)]
        closing = re.fullmatch(
            rf"{re.escape(fence_character)}{{{minimum_length},}}[ \t]*", stripped
        )
        output.append(_blank_line(line))
        if closing and _indentation_columns(indentation_prefix) <= 3:
            fence_character = None
            minimum_length = 0
    return "".join(output)


def _extract_destination(raw: str) -> str | None:
    """Extract a Markdown link destination while ignoring an optional title."""
    value = raw.strip()
    if not value:
        return None
    if value.startswith("<"):
        end = value.find(">")
        return value[1:end] if end > 0 else None
    escaped = False
    depth = 0
    for index, character in enumerate(value):
        if escaped:
            escaped = False
        elif character == "\\":
            escaped = True
        elif character == "(":
            depth += 1
        elif character == ")" and depth:
            depth -= 1
        elif character.isspace() and depth == 0:
            return value[:index]
    return value


def _is_escaped(text: str, index: int) -> bool:
    """Return whether the character at ``index`` has an odd backslash prefix."""
    backslashes = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        backslashes += 1
        cursor -= 1
    return backslashes % 2 == 1


def strip_inline_code_spans(text: str) -> str:
    """Blank CommonMark code spans while preserving line endings and offsets."""
    output = list(text)
    index = 0
    while index < len(text):
        if text[index] != "`" or _is_escaped(text, index):
            index += 1
            continue
        run_end = index
        while run_end < len(text) and text[run_end] == "`":
            run_end += 1
        run_length = run_end - index
        cursor = run_end
        closing_start: int | None = None
        while cursor < len(text):
            if text[cursor] != "`":
                cursor += 1
                continue
            closing_end = cursor
            while closing_end < len(text) and text[closing_end] == "`":
                closing_end += 1
            if closing_end - cursor == run_length:
                closing_start = cursor
                break
            cursor = closing_end
        if closing_start is None:
            index = run_end
            continue
        span_end = closing_start + run_length
        for position in range(index, span_end):
            if output[position] not in {"\n", "\r"}:
                output[position] = " "
        index = span_end
    return "".join(output)


def _matching_open_bracket(text: str, close_index: int) -> int | None:
    """Find the opening bracket for a possibly nested inline-link label."""
    depth = 1
    for index in range(close_index - 1, -1, -1):
        if _is_escaped(text, index):
            continue
        if text[index] == "]":
            depth += 1
        elif text[index] == "[":
            depth -= 1
            if depth == 0:
                return index
    return None


def iter_inline_link_destinations(text: str) -> Iterator[str]:
    """Yield destinations from inline Markdown links using balanced parentheses."""
    cursor = 0
    while True:
        close_bracket = text.find("](", cursor)
        if close_bracket < 0:
            return
        open_bracket = _matching_open_bracket(text, close_bracket)
        if open_bracket is None or _is_escaped(text, open_bracket):
            cursor = close_bracket + 2
            continue
        if (
            open_bracket > 0
            and text[open_bracket - 1] == "!"
            and not _is_escaped(text, open_bracket - 1)
        ):
            cursor = close_bracket + 2
            continue
        start = close_bracket + 2
        depth = 1
        escaped = False
        quote: str | None = None
        index = start
        while index < len(text):
            character = text[index]
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif quote:
                if character == quote:
                    quote = None
            elif character in {'"', "'"}:
                quote = character
            elif character == "(":
                depth += 1
            elif character == ")":
                depth -= 1
                if depth == 0:
                    destination = _extract_destination(text[start:index])
                    if destination:
                        yield destination
                    cursor = index + 1
                    break
            index += 1
        else:
            cursor = start


def _normalize_reference_label(label: str) -> str:
    """Normalize a CommonMark reference label for case-insensitive matching."""
    return " ".join(label.replace("\\", "").split()).casefold()


def _is_image_label(text: str, index: int) -> bool:
    """Return whether a bracketed label is preceded by an unescaped image marker."""
    return index > 0 and text[index - 1] == "!" and not _is_escaped(text, index - 1)


def iter_reference_link_destinations(text: str) -> Iterator[str]:
    """Yield destinations used by full, collapsed, and shortcut reference links."""
    definitions: dict[str, str] = {}
    definition_spans: list[tuple[int, int]] = []
    for match in REFERENCE_DEFINITION.finditer(text):
        destination = _extract_destination(match.group("raw"))
        if destination:
            definitions[_normalize_reference_label(match.group("label"))] = destination
            definition_spans.append(match.span())

    for match in REFERENCE_LINK.finditer(text):
        if _is_escaped(text, match.start()) or _is_image_label(text, match.start()):
            continue
        reference = match.group("reference") or match.group("label")
        destination = definitions.get(_normalize_reference_label(reference))
        if destination:
            yield destination

    for match in BRACKETED_LABEL.finditer(text):
        start, end = match.span()
        if _is_escaped(text, start) or _is_image_label(text, start):
            continue
        if start > 0 and text[start - 1] == "]" and not _is_escaped(text, start - 1):
            continue
        if any(span_start <= start < span_end for span_start, span_end in definition_spans):
            continue
        if text[end : end + 1] in {"(", "[", ":"}:
            continue
        destination = definitions.get(_normalize_reference_label(match.group("label")))
        if destination:
            yield destination


def iter_link_destinations(text: str) -> Iterator[str]:
    """Yield inline and reference-style Markdown link destinations."""
    yield from iter_inline_link_destinations(text)
    yield from iter_reference_link_destinations(text)


def _has_explicit_verification(metadata: Mapping[str, Any], body: str) -> bool:
    """Return whether a document states a concrete verification method."""
    verification = metadata.get("verification")
    if isinstance(verification, str) and verification.strip():
        return True
    if isinstance(verification, (list, dict)) and verification:
        return True
    match = re.search(
        r"^##\s+Verification\s*$\n(?P<content>.*?)(?=^##\s+|\Z)",
        body,
        re.M | re.S | re.I,
    )
    return bool(match and match.group("content").strip())


def validate(path: Path) -> list[Finding]:
    """Validate one governed Markdown document."""
    text = path.read_text(encoding="utf-8")
    if path.name in EXEMPT_NAMES:
        return []
    match = FRONTMATTER.search(text)
    if not match:
        return [Finding(path, "missing YAML frontmatter")]
    try:
        metadata = yaml.safe_load(match.group(1)) or {}
    except yaml.YAMLError as exc:
        return [Finding(path, f"invalid YAML: {exc}")]
    if not isinstance(metadata, dict):
        return [Finding(path, "frontmatter must be a mapping")]

    findings: list[Finding] = []
    missing = sorted(field for field in REQUIRED if not metadata.get(field))
    if missing:
        findings.append(Finding(path, f"missing required fields: {', '.join(missing)}"))

    description = metadata.get("description")
    if not isinstance(description, str) or not description.strip():
        findings.append(Finding(path, "description must be a non-empty string"))

    owners = metadata.get("owners")
    if not (
        isinstance(owners, list)
        and owners
        and all(isinstance(owner, str) and owner.strip() for owner in owners)
    ):
        findings.append(Finding(path, "owners must be a non-empty list of role or team names"))

    doc_type = metadata.get("type")
    doc_id = metadata.get("doc_id")
    status = metadata.get("status")
    rigor = metadata.get("rigor")
    if not isinstance(doc_type, str) or doc_type not in VALID_TYPES:
        findings.append(Finding(path, f"invalid type: {doc_type}"))
    if not isinstance(doc_id, str) or not DOC_ID.fullmatch(doc_id):
        findings.append(Finding(path, f"invalid doc_id: {doc_id}"))
    elif (
        isinstance(doc_type, str)
        and doc_type in VALID_TYPES
        and not doc_id.startswith(f"{doc_type}.")
    ):
        findings.append(Finding(path, "doc_id prefix does not match type"))
    if not isinstance(status, str) or status not in VALID_STATUS:
        findings.append(Finding(path, f"invalid status: {status}"))
    if not isinstance(rigor, str) or rigor not in VALID_RIGOR:
        findings.append(Finding(path, f"invalid rigor: {rigor}"))

    authored = sorted(AUTOMATION_FIELDS.intersection(metadata))
    if authored:
        findings.append(Finding(path, f"automation-owned fields: {', '.join(authored)}"))

    body = text[match.end() :]
    structural_body = strip_inline_code_spans(strip_fenced_blocks(body))
    headings = HEADING.findall(structural_body)
    if sum(level == "#" for level, _ in headings) != 1:
        findings.append(Finding(path, "expected exactly one H1"))
    normalized = [re.sub(r"[^a-z0-9]+", " ", title.lower()).strip() for _, title in headings]
    duplicates = sorted({title for title in normalized if normalized.count(title) > 1})
    if duplicates:
        findings.append(Finding(path, f"duplicate headings: {', '.join(duplicates)}"))

    for target in iter_link_destinations(structural_body):
        target = unquote(target.split("#", 1)[0])
        if not target or re.match(r"^[a-z][a-z0-9+.-]*:", target, re.I):
            continue
        if not (path.parent / target).resolve().exists():
            findings.append(Finding(path, f"broken relative link: {target}"))

    if (
        isinstance(rigor, str)
        and rigor in {"operational", "normative"}
        and not _has_explicit_verification(metadata, structural_body)
    ):
        findings.append(Finding(path, "missing explicit verification method"))
    return findings


def main() -> int:
    """Run validation for command-line inputs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    paths, findings = collect_files(args.inputs)
    findings.extend(finding for path in paths for finding in validate(path))
    for finding in findings:
        print(f"{finding.path}: {finding.message}", file=sys.stderr)
    print(f"validated {len(paths)} markdown files; findings: {len(findings)}")
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
