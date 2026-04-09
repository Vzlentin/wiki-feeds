from __future__ import annotations

import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlparse

import httpx
import yaml

from wiki_feeds.config import ArxivFeedConfig
from wiki_feeds.state import State

NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}

# arXiv API date-range query template (for backfill)
ARXIV_SEARCH_URL = (
    "https://export.arxiv.org/api/query"
    "?search_query={query}+AND+submittedDate:[{start}+TO+{end}]"
    "&sortBy=submittedDate&sortOrder=descending&max_results={max_results}&start={offset}"
)


def _arxiv_id(entry_id: str) -> str:
    """Extract bare arXiv ID from atom:id URL, e.g. http://arxiv.org/abs/2106.00170v1 -> 2106.00170."""
    return re.sub(r"v\d+$", "", entry_id.split("/abs/")[-1])


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return all(kw in text_lower for kw in keywords)


def _parse_feed_xml(xml_text: str) -> list[dict]:
    root = ET.fromstring(xml_text)
    entries = []
    for entry in root.findall("atom:entry", NS):
        entry_id = entry.findtext("atom:id", "", NS)
        title = (entry.findtext("atom:title", "", NS) or "").strip()
        summary = (entry.findtext("atom:summary", "", NS) or "").strip()
        published = entry.findtext("atom:published", "", NS) or ""
        authors = [
            a.findtext("atom:name", "", NS)
            for a in entry.findall("atom:author", NS)
        ]
        # PDF link
        pdf_url = None
        for link in entry.findall("atom:link", NS):
            if link.attrib.get("title") == "pdf":
                pdf_url = link.attrib.get("href", "")
                if pdf_url and not pdf_url.startswith("http"):
                    pdf_url = "https://arxiv.org/pdf/" + _arxiv_id(entry_id)
                break
        if not pdf_url:
            arxiv_id = _arxiv_id(entry_id)
            pdf_url = f"https://arxiv.org/pdf/{arxiv_id}"

        entries.append({
            "id": entry_id,
            "arxiv_id": _arxiv_id(entry_id),
            "title": title,
            "abstract": summary,
            "authors": authors,
            "published": published,
            "pdf_url": pdf_url,
        })
    return entries


def sync(
    feeds: list[ArxivFeedConfig],
    keywords: list[str],
    vault_path: Path,
    state: State,
    client: httpx.Client,
) -> list[dict]:
    """Fetch recent arXiv feeds, filter, download PDFs. Returns inbox items."""
    out_dir = vault_path / "_raw" / "feeds" / "arxiv"
    out_dir.mkdir(parents=True, exist_ok=True)
    inbox_items = []

    for feed in feeds:
        resp = None
        for attempt in range(4):
            try:
                resp = client.get(feed.url, timeout=30, follow_redirects=True)
                resp.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 3:
                    wait = 10 * (3 ** attempt)
                    print(f"  [arXiv] Rate limited, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  [arXiv] Failed to fetch {feed.url}: {e}")
                    resp = None
                    break
            except Exception as e:
                print(f"  [arXiv] Failed to fetch {feed.url}: {e}")
                resp = None
                break
        if resp is None:
            continue

        entries = _parse_feed_xml(resp.text)
        for entry in entries:
            url = entry["id"]
            if state.seen(url):
                continue
            if not _matches_keywords(entry["title"] + " " + entry["abstract"], keywords):
                state.mark(url)  # mark as seen even if filtered
                continue

            arxiv_id = entry["arxiv_id"]
            pdf_path = out_dir / f"{arxiv_id}.pdf"
            yaml_path = out_dir / f"{arxiv_id}.yaml"

            if not pdf_path.exists():
                try:
                    pdf_resp = client.get(entry["pdf_url"], timeout=60, follow_redirects=True)
                    pdf_resp.raise_for_status()
                    pdf_path.write_bytes(pdf_resp.content)
                    time.sleep(3)  # be polite to arXiv
                except Exception as e:
                    print(f"  [arXiv] Failed to download {arxiv_id}: {e}")
                    continue

            yaml_path.write_text(yaml.dump({
                "title": entry["title"],
                "authors": entry["authors"],
                "abstract": entry["abstract"],
                "published": entry["published"],
                "arxiv_id": arxiv_id,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
                "pdf_url": entry["pdf_url"],
            }, allow_unicode=True, sort_keys=False))

            state.mark(url)
            rel_path = f"_raw/feeds/arxiv/{arxiv_id}.pdf"
            inbox_items.append({
                "type": "arXiv",
                "title": entry["title"],
                "path": rel_path,
            })
            print(f"  [arXiv] {arxiv_id}: {entry['title'][:60]}")

    return inbox_items


def backfill(
    keywords: list[str],
    vault_path: Path,
    state: State,
    client: httpx.Client,
    since: date,
) -> list[dict]:
    """Backfill arXiv using keyword search with date range."""
    out_dir = vault_path / "_raw" / "feeds" / "arxiv"
    out_dir.mkdir(parents=True, exist_ok=True)
    inbox_items = []

    keyword_query = "+OR+".join(
        f'ti:"{kw}"+OR+abs:"{kw}"' for kw in keywords[:5]  # limit query length
    )
    start_str = since.strftime("%Y%m%d")
    end_str = date.today().strftime("%Y%m%d")

    offset = 0
    batch = 50
    while True:
        url = ARXIV_SEARCH_URL.format(
            query=keyword_query,
            start=start_str,
            end=end_str,
            max_results=batch,
            offset=offset,
        )
        page_resp = None
        for attempt in range(4):
            try:
                page_resp = client.get(url, timeout=30, follow_redirects=True)
                page_resp.raise_for_status()
                break
            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429 and attempt < 3:
                    wait = 10 * (3 ** attempt)
                    print(f"  [arXiv backfill] Rate limited, retrying in {wait}s...")
                    time.sleep(wait)
                else:
                    print(f"  [arXiv backfill] Request failed: {e}")
                    page_resp = None
                    break
            except Exception as e:
                print(f"  [arXiv backfill] Request failed: {e}")
                page_resp = None
                break
        if page_resp is None:
            break

        entries = _parse_feed_xml(page_resp.text)
        if not entries:
            break

        for entry in entries:
            entry_url = entry["id"]
            if state.seen(entry_url):
                continue
            if not _matches_keywords(entry["title"] + " " + entry["abstract"], keywords):
                state.mark(entry_url)
                continue

            arxiv_id = entry["arxiv_id"]
            pdf_path = out_dir / f"{arxiv_id}.pdf"
            yaml_path = out_dir / f"{arxiv_id}.yaml"

            if not pdf_path.exists():
                try:
                    pdf_resp = client.get(entry["pdf_url"], timeout=60, follow_redirects=True)
                    pdf_resp.raise_for_status()
                    pdf_path.write_bytes(pdf_resp.content)
                    time.sleep(3)
                except Exception as e:
                    print(f"  [arXiv backfill] Failed to download {arxiv_id}: {e}")
                    state.mark(entry_url)
                    continue

            yaml_path.write_text(yaml.dump({
                "title": entry["title"],
                "authors": entry["authors"],
                "abstract": entry["abstract"],
                "published": entry["published"],
                "arxiv_id": arxiv_id,
                "url": f"https://arxiv.org/abs/{arxiv_id}",
            }, allow_unicode=True, sort_keys=False))

            state.mark(entry_url)
            rel_path = f"_raw/feeds/arxiv/{arxiv_id}.pdf"
            inbox_items.append({
                "type": "arXiv",
                "title": entry["title"],
                "path": rel_path,
            })
            print(f"  [arXiv backfill] {arxiv_id}: {entry['title'][:60]}")

        offset += batch
        time.sleep(5)  # arXiv rate limit: 1 req/3s; be conservative

        if len(entries) < batch:
            break

    return inbox_items
