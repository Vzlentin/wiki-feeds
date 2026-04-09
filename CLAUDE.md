# CLAUDE.md

## Commands

This project uses `uv` for dependency management and packaging.

```bash
# Install dependencies
uv sync

# Run the CLI
uv run wiki-feeds --help
uv run wiki-feeds sync
uv run wiki-feeds backfill --since 2025-01-01
uv run wiki-feeds --config /path/to/config.yaml sync

# Run with a specific config
uv run wiki-feeds -c config.yaml sync
```

There are no tests yet.

## Architecture

`wiki-feeds` is a CLI tool that fetches research content into an Obsidian vault. The flow is:

1. **Config** (`config.py`) — loads `config.yaml` into typed dataclasses: `vault_path`, `keywords`, and feed lists for arXiv and blogs.
2. **State** (`state.py`) — a `state.json` file persists a set of seen URLs to avoid re-fetching across runs.
3. **Feed modules** (`feeds/arxiv.py`, `feeds/blogs.py`) — each exposes `sync()` and `backfill()`. They filter entries by keyword match and download content to the vault.
   - arXiv: fetches Atom XML from the arXiv API, downloads PDFs to `_raw/feeds/arxiv/`, writes a sidecar `.yaml` with metadata.
   - Blogs: uses `feedparser` to parse RSS, fetches full article HTML via `httpx`, extracts readable content with `readability-lxml`, converts to Markdown with `markdownify`, saves to `_raw/articles/`. Backfill attempts sitemap discovery (`sitemap.xml` / `sitemap_index.xml`) before falling back to RSS.
4. **Inbox** (`inbox.py`) — appends a dated list of new items to `_raw/_inbox.md` in the vault.
5. **Git** (`git.py`) — stages `_raw/`, commits, and pushes the vault repo after a successful sync.
6. **CLI** (`cli.py`) — `click` group with `sync` and `backfill` subcommands. Both write to the same vault and share the `State` instance.

### Config file structure

```yaml
vault_path: /path/to/obsidian-vault
keywords:
  - conformal prediction
  - ...
feeds:
  arxiv:
    - url: "https://export.arxiv.org/api/query?..."
  blogs:
    - url: "https://example.com/feed.xml"
      name: "Author Name"
```

The default config path is `config.yaml` at the repo root. Pass `-c` to override.

### Key design notes

- `state.json` lives in the **current working directory** when the CLI is invoked, not inside the vault.
- Keyword matching is substring-based (lowercase), applied to title + abstract/content.
- Non-matching arXiv entries are still marked as seen; non-matching blog entries are also marked seen. This prevents re-scanning on every run.
- arXiv backfill queries the search API with `submittedDate` ranges; only the first 5 keywords are used in the query to stay within URL length limits.
- Rate limiting: 3 s sleep between arXiv PDF downloads; 5 s between arXiv search pages; 1 s between blog fetches.
