#!/usr/bin/env python3
"""Create, update, remove, and validate Caesar knowledge-map nodes."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path.cwd().resolve()
DOCS_ROOT = ROOT / "docs"
KM_ID_RE = re.compile(r"^[a-z][a-z0-9-]*\.[a-z0-9][a-z0-9.-]*$")
MARKDOWN_LINK_RE = re.compile(r"\[[^]]*]\(([^)]+)\)")
KM_LINK_RE = re.compile(r"KM:([a-z][a-z0-9-]*\.[a-z0-9][a-z0-9.-]*)")
CODE_LINK_RE = re.compile(r"CODE:([A-Za-z0-9_./-]+)")
KM_TYPES = {
    "map",
    "domain",
    "workflow",
    "invariant",
    "decision",
    "runbook",
    "quality",
    "reference",
    "memory",
}
STATUSES = {"active", "draft", "stale", "deprecated"}
REQUIRED_FIELDS = (
    "km_id",
    "km_type",
    "domain",
    "status",
    "owner",
    "last_verified",
    "source_of_truth",
    "validated_by",
    "tags",
    "related",
)
REQUIRED_PATHS = {
    "docs/index.md",
    "docs/map/index.md",
    "docs/map/schema.md",
    "docs/map/workflows.md",
    "docs/map/domains.md",
    "docs/map/invariants.md",
    "docs/map/glossary.md",
    "docs/workflows/knowledge-query.md",
    "docs/workflows/code-locating.md",
    "docs/workflows/impact-map.md",
    "docs/workflows/code-writing-review.md",
    "docs/workflows/knowledge-map-maintenance.md",
    "docs/memory/index.md",
    "docs/quality/lint-rules.md",
    "docs/quality/stale-docs.md",
    "docs/runbooks/docs-lint.md",
    "docs/decisions/ADR-0001-knowledge-map-structure.md",
}


class DocsError(RuntimeError):
    pass


def repo_path(raw: str | Path, *, must_exist: bool = False) -> Path:
    path = Path(raw)
    resolved = (path if path.is_absolute() else ROOT / path).resolve()
    try:
        resolved.relative_to(ROOT)
    except ValueError as exc:
        raise DocsError(f"path must stay inside repository: {raw}") from exc
    if must_exist and not resolved.exists():
        raise DocsError(f"path does not exist: {resolved}")
    return resolved


def relative(path: Path) -> str:
    return path.resolve().relative_to(ROOT).as_posix()


def single_line(value: Any, name: str) -> str:
    text = str(value).strip()
    if not text or "\n" in text or "\r" in text:
        raise DocsError(f"{name} must be a non-empty single line")
    return text


def string_list(value: Any, name: str, *, non_empty: bool = False) -> list[str]:
    if not isinstance(value, list) or (non_empty and not value):
        qualifier = "a non-empty" if non_empty else "an"
        raise DocsError(f"{name} must be {qualifier} array")
    return [single_line(item, name) for item in value]


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise DocsError(f"missing JSON specification: {path}")
    if path.stat().st_size > 2_000_000:
        raise DocsError(f"JSON specification exceeds 2MB: {path}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DocsError(f"invalid JSON specification: {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DocsError("JSON specification root must be an object")
    return value


def atomic_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def snapshot(path: Path) -> bytes | None:
    return path.read_bytes() if path.is_file() else None


def rollback(path: Path, previous: bytes | None, operation: Callable[[], None]) -> None:
    try:
        operation()
    except Exception:
        if previous is None:
            path.unlink(missing_ok=True)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(previous)
        raise


def validate_spec(spec: dict[str, Any]) -> None:
    required = (
        *REQUIRED_FIELDS[:5],
        "source_of_truth",
        "validated_by",
        "tags",
        "related",
        "title",
        "body",
    )
    missing = [key for key in required if key not in spec]
    if missing:
        raise DocsError(f"node spec missing keys: {missing}")
    km_id = single_line(spec["km_id"], "km_id")
    km_type = single_line(spec["km_type"], "km_type")
    status = single_line(spec["status"], "status")
    if not KM_ID_RE.fullmatch(km_id) or km_id.split(".", 1)[0] != km_type:
        raise DocsError("km_id is invalid or does not match km_type")
    if km_type not in KM_TYPES:
        raise DocsError(f"unsupported km_type: {km_type}")
    if status not in STATUSES:
        raise DocsError(f"unsupported status: {status}")
    for key in ("domain", "owner", "title"):
        single_line(spec[key], key)
    for key in ("source_of_truth", "validated_by", "tags", "related"):
        string_list(
            spec[key], key, non_empty=key in {"source_of_truth", "validated_by", "tags"}
        )
    if not isinstance(spec["body"], str) or not spec["body"].strip():
        raise DocsError("body must be a non-empty string")


def render_node(spec: dict[str, Any]) -> str:
    validate_spec(spec)
    lines = [
        "---",
        f"km_id: {single_line(spec['km_id'], 'km_id')}",
        f"km_type: {single_line(spec['km_type'], 'km_type')}",
        f"domain: {single_line(spec['domain'], 'domain')}",
        f"status: {single_line(spec['status'], 'status')}",
        f"owner: {single_line(spec['owner'], 'owner')}",
        f"last_verified: {datetime.now().astimezone().date().isoformat()}",
    ]
    for key in ("source_of_truth", "validated_by", "tags", "related"):
        lines.append(f"{key}:")
        lines.extend(f"  - {item}" for item in string_list(spec[key], key))
    lines.extend(
        (
            "---",
            "",
            f"# {single_line(spec['title'], 'title')}",
            "",
            spec["body"].strip(),
            "",
        )
    )
    return "\n".join(lines)


def parse_frontmatter(path: Path) -> tuple[dict[str, Any], str]:
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        raise DocsError(f"{relative(path)}: missing opening frontmatter fence")
    try:
        end = next(
            index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"
        )
    except StopIteration as exc:
        raise DocsError(f"{relative(path)}: missing closing frontmatter fence") from exc
    values: dict[str, Any] = {}
    current_list: str | None = None
    for line in lines[1:end]:
        if line.startswith("  - ") and current_list:
            values[current_list].append(line[4:].strip())
            continue
        match = re.fullmatch(r"([a-z_]+):(?:\s*(.*))?", line)
        if not match:
            raise DocsError(f"{relative(path)}: unsupported frontmatter line: {line}")
        key, raw_value = match.groups()
        if raw_value:
            values[key] = raw_value.strip().strip("\"'")
            current_list = None
        else:
            values[key] = []
            current_list = key
    return values, text


def validate_docs() -> list[str]:
    if not DOCS_ROOT.is_dir():
        return ["docs/: knowledge map not found"]
    paths, scope_errors, _ = knowledge_paths()
    if not paths:
        return ["docs/: no Markdown knowledge nodes found"]
    errors: list[str] = list(scope_errors)
    nodes: dict[str, Path] = {}
    parsed: list[tuple[Path, dict[str, Any], str]] = []
    for path in paths:
        try:
            fields, text = parse_frontmatter(path)
        except DocsError as exc:
            errors.append(str(exc))
            continue
        parsed.append((path, fields, text))
        missing = [field for field in REQUIRED_FIELDS if field not in fields]
        if missing:
            errors.append(f"{relative(path)}: missing frontmatter fields {missing}")
            continue
        km_id = str(fields["km_id"])
        km_type = str(fields["km_type"])
        if not KM_ID_RE.fullmatch(km_id) or km_id.split(".", 1)[0] != km_type:
            errors.append(
                f"{relative(path)}: invalid km_id/km_type pair {km_id}/{km_type}"
            )
        if km_type not in KM_TYPES:
            errors.append(f"{relative(path)}: unsupported km_type {km_type}")
        if fields["status"] not in STATUSES:
            errors.append(f"{relative(path)}: unsupported status {fields['status']}")
        if km_id in nodes:
            errors.append(
                f"{relative(path)}: duplicate km_id {km_id}; first in {relative(nodes[km_id])}"
            )
        else:
            nodes[km_id] = path

    for path, fields, text in parsed:
        for related in fields.get("related", []):
            if related not in nodes:
                errors.append(f"{relative(path)}: unresolved related id {related}")
        for km_id in KM_LINK_RE.findall(text):
            if km_id not in nodes:
                errors.append(f"{relative(path)}: unresolved KM link {km_id}")
        for target in MARKDOWN_LINK_RE.findall(text):
            target = target.split("#", 1)[0].strip()
            if not target or target.startswith(
                ("http://", "https://", "mailto:", "data:")
            ):
                continue
            if not (path.parent / target).resolve().exists():
                errors.append(f"{relative(path)}: missing relative link {target}")
        for code_path in CODE_LINK_RE.findall(text):
            if not repo_path(code_path).exists():
                errors.append(f"{relative(path)}: missing CODE path {code_path}")
        for source in fields.get("source_of_truth", []):
            if source.startswith(("command:", "manual-", "user:")):
                continue
            source_path = source.split("#", 1)[0]
            if not repo_path(source_path).exists():
                errors.append(
                    f"{relative(path)}: missing source_of_truth path {source}"
                )
    return sorted(set(errors))


def knowledge_paths() -> tuple[list[Path], list[str], list[Path]]:
    """Explicit legacy inventory permits incremental maps without hiding new files."""
    paths = sorted(DOCS_ROOT.rglob("*.md"))
    config = DOCS_ROOT / "map" / "legacy-documents.json"
    if not config.exists():
        return paths, [], []
    value = load_json(config)
    if value.get("version") != 1:
        raise DocsError("legacy-documents.json: unsupported version")
    legacy = string_list(value.get("documents"), "documents")
    if len(legacy) != len(set(legacy)):
        raise DocsError("legacy-documents.json: duplicate document")
    excluded: list[Path] = []
    errors: list[str] = []
    for raw in legacy:
        path = repo_path(raw)
        if DOCS_ROOT not in path.parents or path.suffix != ".md" or not path.is_file():
            errors.append(f"invalid legacy document: {raw}")
            continue
        if re.search(r"^km_id:", path.read_text(encoding="utf-8"), re.MULTILINE):
            errors.append(f"managed node cannot be excluded as legacy: {raw}")
            continue
        excluded.append(path)
    return [p for p in paths if p.resolve() not in excluded], errors, excluded


def require_valid_docs() -> None:
    errors = validate_docs()
    if errors:
        raise DocsError("knowledge-map validation failed:\n- " + "\n- ".join(errors))


def template_command(args: argparse.Namespace) -> None:
    path = repo_path(args.output)
    if path.exists():
        raise DocsError(f"template output already exists: {path}")
    if path.suffix != ".json":
        raise DocsError("template output must use .json")
    value = {
        "km_id": "reference.replace-me",
        "km_type": "reference",
        "domain": "code",
        "status": "draft",
        "owner": "maintainers",
        "source_of_truth": ["replace-with-repository-path"],
        "validated_by": ["command:caesar-docs validate"],
        "tags": ["reference:replace-me"],
        "related": [],
        "title": "Replace Me",
        "body": "Replace with durable, verified Markdown facts.",
    }
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    print(f"template emitted: {relative(path)}")


def upsert_command(args: argparse.Namespace) -> None:
    path = repo_path(args.path)
    if path.suffix != ".md" or DOCS_ROOT not in (path, *path.parents):
        raise DocsError("managed knowledge nodes must be Markdown under docs/")
    spec = load_json(repo_path(args.spec, must_exist=True))
    previous = snapshot(path)
    if path.exists():
        fields, _ = parse_frontmatter(path)
        actual = fields.get("km_id")
        if not args.expect_km_id:
            raise DocsError("updating an existing node requires --expect-km-id")
        if args.expect_km_id != actual or spec.get("km_id") != actual:
            raise DocsError(
                f"managed node identity is immutable: existing={actual}, "
                f"expected={args.expect_km_id}, input={spec.get('km_id')}"
            )

    def operation() -> None:
        atomic_text(path, render_node(spec))
        require_valid_docs()

    rollback(path, previous, operation)
    print(f"node upserted: {relative(path)} ({spec['km_id']})")


def remove_command(args: argparse.Namespace) -> None:
    path = repo_path(args.path, must_exist=True)
    relative_path = relative(path)
    if path.suffix != ".md" or DOCS_ROOT not in (path, *path.parents):
        raise DocsError("managed knowledge nodes must be Markdown under docs/")
    if relative_path in REQUIRED_PATHS:
        raise DocsError(
            f"required knowledge-map node cannot be removed: {relative_path}"
        )
    fields, _ = parse_frontmatter(path)
    actual = fields.get("km_id")
    if actual != args.expect_km_id:
        raise DocsError(f"km_id mismatch: expected {args.expect_km_id}, found {actual}")
    if args.confirm != f"delete:{actual}":
        raise DocsError(f"confirmation must equal delete:{actual}")
    if not args.reason.strip():
        raise DocsError("deletion reason is required")
    inbound: list[str] = []
    for candidate in sorted(DOCS_ROOT.rglob("*.md")):
        if candidate.resolve() == path:
            continue
        text = candidate.read_text(encoding="utf-8")
        if f"KM:{actual}" in text or re.search(
            rf"^\s*-\s+{re.escape(str(actual))}\s*$", text, re.MULTILINE
        ):
            inbound.append(relative(candidate))
    if inbound:
        raise DocsError(f"node has inbound references: {inbound}")
    previous = snapshot(path)

    def operation() -> None:
        path.unlink()
        require_valid_docs()

    rollback(path, previous, operation)
    print(f"node removed: {relative_path} ({actual}); reason={args.reason.strip()}")


def validate_command(_args: argparse.Namespace) -> None:
    require_valid_docs()
    paths, _, legacy = knowledge_paths()
    print(f"knowledge map valid: {len(paths)} managed nodes; {len(legacy)} legacy documents (content not validated)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    template = subparsers.add_parser(
        "template", help="Emit a JSON knowledge-node template."
    )
    template.add_argument("--output", required=True)
    template.set_defaults(handler=template_command)

    upsert = subparsers.add_parser(
        "node-upsert", help="Create or replace one validated knowledge node."
    )
    upsert.add_argument("--path", required=True)
    upsert.add_argument("--spec", required=True)
    upsert.add_argument("--expect-km-id")
    upsert.set_defaults(handler=upsert_command)

    remove = subparsers.add_parser(
        "node-remove", help="Delete an unreferenced, non-required knowledge node."
    )
    remove.add_argument("--path", required=True)
    remove.add_argument("--expect-km-id", required=True)
    remove.add_argument("--reason", required=True)
    remove.add_argument("--confirm", required=True)
    remove.set_defaults(handler=remove_command)

    validate = subparsers.add_parser(
        "validate", help="Validate all Markdown nodes under docs/."
    )
    validate.set_defaults(handler=validate_command)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    args.handler(args)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (DocsError, OSError, ValueError, KeyError) as exc:
        print(f"ERROR {exc}", file=sys.stderr)
        raise SystemExit(1) from None
