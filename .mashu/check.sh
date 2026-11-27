#!/usr/bin/env bash
set -eu
cd "$(dirname "$0")/.."
python3 -m unittest discover -s tests || { echo 'ERROR 麻薯单测失败'; exit 1; }
if [[ "${MASHU_DOCS_MANAGED:-}" != 1 ]]; then
  python3 skills/caesar-docs/scripts/caesar_docs.py validate
fi
