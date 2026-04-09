from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class ArxivFeedConfig:
    url: str


@dataclass
class BlogFeedConfig:
    url: str
    name: str


@dataclass
class FeedsConfig:
    arxiv: list[ArxivFeedConfig] = field(default_factory=list)
    blogs: list[BlogFeedConfig] = field(default_factory=list)


@dataclass
class Config:
    vault_path: Path
    keywords: list[str]
    feeds: FeedsConfig


def load(path: Path) -> Config:
    raw = yaml.safe_load(path.read_text())

    feeds_raw = raw.get("feeds", {})
    arxiv = [ArxivFeedConfig(url=f["url"]) for f in feeds_raw.get("arxiv", [])]
    blogs = [BlogFeedConfig(url=f["url"], name=f["name"]) for f in feeds_raw.get("blogs", [])]

    return Config(
        vault_path=Path(raw["vault_path"]),
        keywords=[kw.lower() for kw in raw.get("keywords", [])],
        feeds=FeedsConfig(arxiv=arxiv, blogs=blogs),
    )
