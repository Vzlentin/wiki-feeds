from __future__ import annotations

from datetime import date
from pathlib import Path


def append(vault_path: Path, items: list[dict]) -> None:
    """Append new items to _raw/_inbox.md.

    Each item: {"type": "arXiv"|"Blog", "title": str, "author": str|None, "path": str}
    """
    if not items:
        return

    inbox = vault_path / "_raw" / "_inbox.md"

    # Read existing content (or start fresh)
    if inbox.exists():
        existing = inbox.read_text()
    else:
        existing = "# Inbox\n\nNew sources ready for wiki ingest. Remove entries as you process them.\n"

    today = date.today().isoformat()
    lines = [f"\n### {today}\n"]
    for item in items:
        source_type = item["type"]
        title = item["title"]
        path = item["path"]
        author = item.get("author")
        if author:
            lines.append(f"- **[{source_type}]** \"{title}\" ({author}) — `{path}`")
        else:
            lines.append(f"- **[{source_type}]** \"{title}\" — `{path}`")

    inbox.write_text(existing + "\n".join(lines) + "\n")
