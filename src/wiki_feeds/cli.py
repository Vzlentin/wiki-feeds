from __future__ import annotations

from datetime import date
from pathlib import Path

import click
import httpx

from wiki_feeds import config as cfg_module
from wiki_feeds import git, inbox
from wiki_feeds.feeds import arxiv, blogs
from wiki_feeds.state import State

DEFAULT_CONFIG = Path(__file__).parent.parent.parent / "config.yaml"


@click.group()
@click.option("--config", "-c", type=click.Path(exists=True, path_type=Path), default=None)
@click.pass_context
def main(ctx: click.Context, config: Path | None) -> None:
    """wiki-feeds: automated source fetcher for the LLM wiki."""
    config_path = config or DEFAULT_CONFIG
    ctx.ensure_object(dict)
    ctx.obj["config"] = cfg_module.load(config_path)
    ctx.obj["state"] = State(Path("state.json"))


@main.command()
@click.pass_context
def sync(ctx: click.Context) -> None:
    """Fetch new items from all feeds and push to vault."""
    conf: cfg_module.Config = ctx.obj["config"]
    state: State = ctx.obj["state"]
    vault = conf.vault_path

    if not vault.exists():
        raise click.ClickException(f"Vault path does not exist: {vault}")

    inbox_items: list[dict] = []

    with httpx.Client(headers={"User-Agent": "wiki-feeds/1.0 (personal research tool)"}) as client:
        print("Fetching arXiv feeds...")
        inbox_items += arxiv.sync(conf.feeds.arxiv, conf.keywords, vault, state, client)

        print("Fetching blog feeds...")
        inbox_items += blogs.sync(conf.feeds.blogs, conf.keywords, vault, state, client)

    state.save()

    if inbox_items:
        inbox.append(vault, inbox_items)
        git.commit_and_push(vault, f"wiki-feeds: add {len(inbox_items)} new source(s)")
        print(f"\nDone. {len(inbox_items)} new item(s) added to _raw/_inbox.md")
    else:
        print("\nDone. No new matching items found.")


@main.command()
@click.option("--since", required=True, type=click.DateTime(formats=["%Y-%m-%d"]), help="Start date for backfill (YYYY-MM-DD)")
@click.option("--arxiv-only", is_flag=True, default=False, help="Only backfill arXiv (skip blogs)")
@click.option("--blogs-only", is_flag=True, default=False, help="Only backfill blogs (skip arXiv)")
@click.pass_context
def backfill(ctx: click.Context, since: date, arxiv_only: bool, blogs_only: bool) -> None:
    """Backfill historical sources since a given date."""
    conf: cfg_module.Config = ctx.obj["config"]
    state: State = ctx.obj["state"]
    vault = conf.vault_path
    since_date = since.date() if hasattr(since, "date") else since

    if not vault.exists():
        raise click.ClickException(f"Vault path does not exist: {vault}")

    inbox_items: list[dict] = []

    with httpx.Client(headers={"User-Agent": "wiki-feeds/1.0 (personal research tool)"}) as client:
        if not blogs_only:
            print(f"Backfilling arXiv since {since_date}...")
            inbox_items += arxiv.backfill(conf.keywords, vault, state, client, since_date)

        if not arxiv_only:
            print(f"Backfilling blogs since {since_date}...")
            inbox_items += blogs.backfill(conf.feeds.blogs, conf.keywords, vault, state, client, since_date)

    state.save()

    if inbox_items:
        inbox.append(vault, inbox_items)
        git.commit_and_push(vault, f"wiki-feeds: backfill {len(inbox_items)} source(s) since {since_date}")
        print(f"\nBackfill done. {len(inbox_items)} item(s) added.")
    else:
        print("\nBackfill done. No new matching items found.")
