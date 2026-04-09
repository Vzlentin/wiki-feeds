from __future__ import annotations

import re
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

import feedparser
import httpx
import markdownify
import yaml
from readability import Document

from wiki_feeds.config import BlogFeedConfig
from wiki_feeds.state import State


def _slug(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_]+", "-", text)
    text = re.sub(r"-+", "-", text)
    return text[:80].rstrip("-")


def _matches_keywords(text: str, keywords: list[str]) -> bool:
    text_lower = text.lower()
    return any(kw in text_lower for kw in keywords)


def _entry_date(entry) -> date | None:
    for attr in ("published_parsed", "updated_parsed"):
        val = getattr(entry, attr, None)
        if val:
            try:
                return date(*val[:3])
            except Exception:
                pass
    return None


def _fetch_and_convert(url: str, client: httpx.Client) -> str | None:
    """Fetch a blog post URL and return cleaned markdown."""
    try:
        resp = client.get(url, timeout=30, follow_redirects=True)
        resp.raise_for_status()
        doc = Document(resp.text)
        html = doc.summary()
        md = markdownify.markdownify(html, heading_style="ATX", strip=["a"])
        # Clean up excessive blank lines
        md = re.sub(r"\n{3,}", "\n\n", md).strip()
        return md
    except Exception as e:
        print(f"    Failed to fetch/convert {url}: {e}")
        return None


def sync(
    feeds: list[BlogFeedConfig],
    keywords: list[str],
    vault_path: Path,
    state: State,
    client: httpx.Client,
) -> list[dict]:
    """Fetch recent blog entries, filter, convert to markdown. Returns inbox items."""
    out_dir = vault_path / "_raw" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    inbox_items = []

    for feed_cfg in feeds:
        print(f"  [Blog] Checking {feed_cfg.name}...")
        try:
            parsed = feedparser.parse(feed_cfg.url)
        except Exception as e:
            print(f"    Failed to parse feed: {e}")
            continue

        for entry in parsed.entries:
            url = entry.get("link", "")
            if not url or state.seen(url):
                continue

            title = entry.get("title", "Untitled")
            content = entry.get("summary", "") + " " + entry.get("content", [{}])[0].get("value", "") if entry.get("content") else entry.get("summary", "")

            if keywords and not _matches_keywords(title + " " + content, keywords):
                state.mark(url)
                continue

            entry_date = _entry_date(entry)
            date_str = entry_date.isoformat() if entry_date else date.today().isoformat()

            author_slug = _slug(feed_cfg.name)
            title_slug = _slug(title)
            filename = f"{author_slug}_{title_slug}.md"
            out_path = out_dir / filename

            if not out_path.exists():
                md_body = _fetch_and_convert(url, client)
                if md_body is None:
                    state.mark(url)
                    continue

                frontmatter = yaml.dump({
                    "title": title,
                    "author": feed_cfg.name,
                    "date": date_str,
                    "source_url": url,
                    "type": "article",
                }, allow_unicode=True, sort_keys=False).strip()

                out_path.write_text(f"---\n{frontmatter}\n---\n\n# {title}\n\n{md_body}\n")
                time.sleep(1)

            state.mark(url)
            rel_path = f"_raw/articles/{filename}"
            inbox_items.append({
                "type": "Blog",
                "title": title,
                "author": feed_cfg.name,
                "path": rel_path,
            })
            print(f"    Saved: {filename}")

    return inbox_items


def backfill(
    feeds: list[BlogFeedConfig],
    keywords: list[str],
    vault_path: Path,
    state: State,
    client: httpx.Client,
    since: date,
) -> list[dict]:
    """Backfill blog posts by attempting sitemap discovery.

    Most blogs don't expose historical RSS beyond recent N posts. This tries:
    1. sitemap.xml
    2. sitemap_index.xml
    Falls back to what the RSS feed currently provides.
    """
    out_dir = vault_path / "_raw" / "articles"
    out_dir.mkdir(parents=True, exist_ok=True)
    inbox_items = []

    for feed_cfg in feeds:
        print(f"  [Blog backfill] {feed_cfg.name}...")
        base = "{uri.scheme}://{uri.netloc}".format(uri=urlparse(feed_cfg.url))
        urls_to_process: list[tuple[str, str | None]] = []  # (url, date_str)

        # Try sitemap
        discovered = _discover_sitemap_urls(base, since, client)
        if discovered:
            urls_to_process = discovered
        else:
            # Fall back to current RSS entries
            try:
                parsed = feedparser.parse(feed_cfg.url)
                for entry in parsed.entries:
                    url = entry.get("link", "")
                    if not url:
                        continue
                    entry_date = _entry_date(entry)
                    if entry_date and entry_date < since:
                        continue
                    urls_to_process.append((url, entry_date.isoformat() if entry_date else None))
            except Exception:
                pass

        for url, date_str in urls_to_process:
            if state.seen(url):
                continue

            # Fetch full content to keyword-match
            try:
                resp = client.get(url, timeout=30, follow_redirects=True)
                resp.raise_for_status()
                doc = Document(resp.text)
                title = doc.title() or url.split("/")[-1]
                html = doc.summary()
            except Exception as e:
                print(f"    Failed to fetch {url}: {e}")
                state.mark(url)
                continue

            if keywords and not _matches_keywords(title + " " + html, keywords):
                state.mark(url)
                time.sleep(0.5)
                continue

            md_body = markdownify.markdownify(html, heading_style="ATX", strip=["a"])
            md_body = re.sub(r"\n{3,}", "\n\n", md_body).strip()

            date_out = date_str or date.today().isoformat()
            author_slug = _slug(feed_cfg.name)
            title_slug = _slug(title)
            filename = f"{author_slug}_{title_slug}.md"
            out_path = out_dir / filename

            if not out_path.exists():
                frontmatter = yaml.dump({
                    "title": title,
                    "author": feed_cfg.name,
                    "date": date_out,
                    "source_url": url,
                    "type": "article",
                }, allow_unicode=True, sort_keys=False).strip()
                out_path.write_text(f"---\n{frontmatter}\n---\n\n# {title}\n\n{md_body}\n")

            state.mark(url)
            rel_path = f"_raw/articles/{filename}"
            inbox_items.append({
                "type": "Blog",
                "title": title,
                "author": feed_cfg.name,
                "path": rel_path,
            })
            print(f"    Saved: {filename}")
            time.sleep(1)

    return inbox_items


def _discover_sitemap_urls(base: str, since: date, client: httpx.Client) -> list[tuple[str, str | None]]:
    """Try to discover post URLs from sitemap.xml / sitemap_index.xml."""
    import xml.etree.ElementTree as ET

    SM_NS = "http://www.sitemaps.org/schemas/sitemap/0.9"

    def try_get(url: str) -> str | None:
        try:
            r = client.get(url, timeout=15, follow_redirects=True)
            if r.status_code == 200 and "xml" in r.headers.get("content-type", ""):
                return r.text
        except Exception:
            pass
        return None

    sitemap_urls = [f"{base}/sitemap.xml", f"{base}/sitemap_index.xml", f"{base}/sitemap-index.xml"]
    results: list[tuple[str, str | None]] = []

    for smap_url in sitemap_urls:
        content = try_get(smap_url)
        if not content:
            continue
        try:
            root = ET.fromstring(content)
        except Exception:
            continue

        # sitemap index: recurse into sub-sitemaps
        sub_sitemaps = root.findall(f"{{{SM_NS}}}sitemap/{{{SM_NS}}}loc")
        if sub_sitemaps:
            for sub_loc in sub_sitemaps:
                sub_content = try_get(sub_loc.text.strip())
                if not sub_content:
                    continue
                try:
                    sub_root = ET.fromstring(sub_content)
                    results.extend(_extract_urls(sub_root, SM_NS, since))
                except Exception:
                    pass
        else:
            results.extend(_extract_urls(root, SM_NS, since))

        if results:
            break

    return results


def _extract_urls(root, ns: str, since: date) -> list[tuple[str, str | None]]:
    results = []
    for url_el in root.findall(f"{{{ns}}}url"):
        loc = url_el.findtext(f"{{{ns}}}loc", "").strip()
        lastmod = url_el.findtext(f"{{{ns}}}lastmod", "").strip()
        if not loc:
            continue
        if lastmod:
            try:
                url_date = date.fromisoformat(lastmod[:10])
                if url_date < since:
                    continue
            except Exception:
                pass
        results.append((loc, lastmod[:10] if lastmod else None))
    return results
