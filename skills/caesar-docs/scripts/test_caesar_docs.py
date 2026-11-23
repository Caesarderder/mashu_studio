#!/usr/bin/env python3
"""Regression tests for the standalone Caesar Docs writer and validator."""

from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any

SCRIPT = Path(__file__).with_name("caesar_docs.py").resolve()
SKILL_ROOT = SCRIPT.parents[1]


class CaesarDocsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "package.json").write_text('{"private": true}\n', encoding="utf-8")

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def run_cli(
        self, *arguments: str, succeeds: bool = True
    ) -> subprocess.CompletedProcess[str]:
        result = subprocess.run(
            [sys.executable, str(SCRIPT), *arguments],
            cwd=self.root,
            capture_output=True,
            text=True,
            check=False,
        )
        if succeeds and result.returncode != 0:
            self.fail(f"command failed: {result.stderr or result.stdout}")
        if not succeeds and result.returncode == 0:
            self.fail("command unexpectedly succeeded")
        return result

    def write_spec(self, name: str, **overrides: Any) -> Path:
        spec: dict[str, Any] = {
            "km_id": "reference.example",
            "km_type": "reference",
            "domain": "code",
            "status": "active",
            "owner": "maintainers",
            "source_of_truth": ["package.json"],
            "validated_by": ["manual-docs-review"],
            "tags": ["reference:example"],
            "related": [],
            "title": "示例节点",
            "body": "这是经过验证的事实。",
        }
        spec.update(overrides)
        path = self.root / name
        path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
        return path

    def test_template_and_node_lifecycle(self) -> None:
        self.run_cli("template", "--output", ".caesar-input/node.json")
        self.assertTrue((self.root / ".caesar-input/node.json").is_file())

        spec = self.write_spec("node.json")
        self.run_cli(
            "node-upsert", "--path", "docs/references/example.md", "--spec", str(spec)
        )
        node = self.root / "docs/references/example.md"
        self.assertIn("km_id: reference.example", node.read_text(encoding="utf-8"))
        self.run_cli("validate")

        updated = self.write_spec("updated.json", body="更新后的已验证事实。")
        self.run_cli(
            "node-upsert",
            "--path",
            "docs/references/example.md",
            "--spec",
            str(updated),
            "--expect-km-id",
            "reference.example",
        )
        self.assertIn("更新后的已验证事实", node.read_text(encoding="utf-8"))

        keeper_spec = self.write_spec(
            "keeper.json",
            km_id="reference.keeper",
            title="保留节点",
            tags=["reference:keeper"],
        )
        self.run_cli(
            "node-upsert",
            "--path",
            "docs/references/keeper.md",
            "--spec",
            str(keeper_spec),
        )
        self.run_cli(
            "node-remove",
            "--path",
            "docs/references/example.md",
            "--expect-km-id",
            "reference.example",
            "--reason",
            "Temporary test node",
            "--confirm",
            "delete:reference.example",
        )
        self.assertFalse(node.exists())

        keeper = self.root / "docs/references/keeper.md"
        self.run_cli(
            "node-remove",
            "--path",
            "docs/references/keeper.md",
            "--expect-km-id",
            "reference.keeper",
            "--reason",
            "Would leave an empty map",
            "--confirm",
            "delete:reference.keeper",
            succeeds=False,
        )
        self.assertTrue(
            keeper.exists(), "removal must roll back when it would leave an empty map"
        )

    def test_rejects_identity_changes_and_rolls_back_invalid_links(self) -> None:
        spec = self.write_spec("node.json")
        self.run_cli(
            "node-upsert", "--path", "docs/references/example.md", "--spec", str(spec)
        )
        node = self.root / "docs/references/example.md"
        original = node.read_bytes()

        changed = self.write_spec(
            "changed.json",
            km_id="reference.renamed",
            body="[KM:reference.missing](missing.md)",
        )
        self.run_cli(
            "node-upsert",
            "--path",
            "docs/references/example.md",
            "--spec",
            str(changed),
            "--expect-km-id",
            "reference.example",
            succeeds=False,
        )
        self.assertEqual(node.read_bytes(), original)

        invalid = self.write_spec("invalid.json", related=["reference.missing"])
        self.run_cli(
            "node-upsert",
            "--path",
            "docs/references/example.md",
            "--spec",
            str(invalid),
            "--expect-km-id",
            "reference.example",
            succeeds=False,
        )
        self.assertEqual(node.read_bytes(), original)

    def test_rejects_paths_outside_repository(self) -> None:
        result = self.run_cli("template", "--output", "../outside.json", succeeds=False)
        self.assertIn("path must stay inside repository", result.stderr)
        self.assertFalse((self.root.parent / "outside.json").exists())

    def test_skill_is_self_contained(self) -> None:
        markdown_files = sorted(SKILL_ROOT.rglob("*.md"))
        self.assertGreater(len(markdown_files), 10)
        for path in markdown_files:
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("caesar-awesome", text, path)
            self.assertNotIn(".codex/", text, path)
            for target in re.findall(r"\[[^]]*]\(([^)]+)\)", text):
                target = target.split("#", 1)[0]
                if not target or target.startswith(("http://", "https://", "mailto:")):
                    continue
                self.assertTrue(
                    (path.parent / target).resolve().exists(),
                    f"{path}: missing {target}",
                )

    def test_legacy_scope_preserves_history_but_does_not_hide_new_nodes(self) -> None:
        spec = self.write_spec("node.json")
        self.run_cli("node-upsert", "--path", "docs/references/example.md", "--spec", str(spec))
        legacy = self.root / "docs/old-record.md"
        original = "# Historical evidence\nNever claim this was reverified.\n"
        legacy.write_text(original)
        config = self.root / "docs/map/legacy-documents.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"version": 1, "documents": ["docs/old-record.md"]}))
        result = self.run_cli("validate")
        self.assertIn("1 managed nodes; 1 legacy documents", result.stdout)
        self.assertEqual(legacy.read_text(), original)
        (self.root / "docs/unregistered.md").write_text("# New untracked doc\n")
        self.run_cli("validate", succeeds=False)
        (self.root / "docs/unregistered.md").unlink()
        config.write_text(json.dumps({"version": 1, "documents": ["docs/old-record.md", "docs/references/example.md"]}))
        self.assertIn("managed node cannot be excluded", self.run_cli("validate", succeeds=False).stderr)

    def test_skill_sync_refuses_local_edits_before_writing(self) -> None:
        source = self.root / "source"
        names = ("caesar-docs", "caesar-builda", "caesar-design", "caesar-art", "caesar-program", "caesar-producer")
        for name in names:
            d = source / name
            d.mkdir(parents=True)
            (d / "SKILL.md").write_text("Original " + name)
            (d / "VERSION").write_text("1.0.0")
        command = [sys.executable, str(SCRIPT.with_name("sync_skills.py")), "--source", str(source), "--project", str(self.root)]
        self.assertEqual(subprocess.run(command, capture_output=True).returncode, 0)
        self.assertFalse((self.root / ".agents/skills").exists())
        self.assertEqual(subprocess.run(command + ["--apply"], capture_output=True).returncode, 0)
        local = self.root / ".agents/skills/caesar-program/SKILL.md"
        local.write_text("User local edit")
        first = self.root / ".agents/skills/caesar-docs/SKILL.md"
        original = first.read_bytes()
        (source / "caesar-docs/SKILL.md").write_text("Updated upstream")
        result = subprocess.run(command + ["--apply"], capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(local.read_text(), "User local edit")
        self.assertEqual(first.read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
