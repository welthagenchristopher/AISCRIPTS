#!/usr/bin/env python3
import json
import re
from pathlib import Path
from datetime import datetime
import argparse
import sys

def main():
    parser = argparse.ArgumentParser(
        description="Sync 'seemore' links and force-update dates in index.json by filename lookup."
    )
    parser.add_argument(
        "--index-file",
        type=Path,
        required=True,
        help="Path to your index.json"
    )
    parser.add_argument(
        "--docs-base",
        type=Path,
        required=True,
        help="Root folder under which all .md docs live"
    )
    args = parser.parse_args()

    idx_path  = args.index_file
    docs_base = args.docs_base

    # Sanity check
    print("Index file:", idx_path.resolve(), "exists?", idx_path.exists())
    print("Docs base:", docs_base.resolve(), "exists?", docs_base.exists())
    if not (idx_path.is_file() and docs_base.is_dir()):
        print("❌ Fix those paths and try again.", file=sys.stderr)
        sys.exit(1)

    data  = json.loads(idx_path.read_text(encoding="utf-8"))
    today = datetime.now().strftime("%Y-%m-%d")

    # Pre-scan all .md docs into filename → [Path] map
    md_files = list(docs_base.rglob("*.md"))
    file_map = {}
    for p in md_files:
        file_map.setdefault(p.name, []).append(p)

    md_link_rx = re.compile(r'\[[^\]]+\]\(\s*(https?://[^\s\)]+)\s*\)')
    raw_url_rx = re.compile(r'(https?://[^\s<]+)')

    for category, entries in data.items():
        if not isinstance(entries, dict):
            print(f"⚠️  Skipping category '{category}' (not an object)")
            continue

        for name, props in entries.items():
            # 1) Skip null or non-dict props
            if not isinstance(props, dict):
                print(f"⚠️  Skipping {category}/{name}: entry is null or not an object")
                continue

            # 2) Skip if no path field
            rel_path = props.get("path")
            if not rel_path:
                print(f"⚠️  Skipping {category}/{name}: no 'path' property")
                continue

            filename = Path(rel_path).name
            matches  = file_map.get(filename, [])

            if len(matches) == 0:
                print(f"⚠️  No .md found for {category}/{name} → looking for '{filename}'")
                continue
            if len(matches) > 1:
                print(f"⚠️  Ambiguous .md for {category}/{name}:")
                for m in matches:
                    print("    ", m)
                continue

            md_path = matches[0]
            text    = md_path.read_text(encoding="utf-8")

            # extract link or raw URL
            m = md_link_rx.search(text) or raw_url_rx.search(text)
            if m:
                props["seemore"] = m.group(1)
            else:
                print(f"⚠️  No URL in {md_path} for {category}/{name}")

            # force today's date
            props["last_updated"] = today

    # write back in-place
    idx_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"✅ Done — synced all docs, set last_updated = {today}")

if __name__ == "__main__":
    main()
