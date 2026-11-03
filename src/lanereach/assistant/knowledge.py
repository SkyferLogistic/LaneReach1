"""The assistant's knowledge: LaneReach's help articles and a node reference built from the code.

The articles are Markdown files shipped with the application (`articles/*.md`, with a small
front matter). The workflow node reference is generated from the live node registry, so what the
assistant says about triggers and steps always matches what the engine can run.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

from lanereach.automation.nodes.base import BaseNode, NodeCategory
from lanereach.automation.nodes.registry import NodeRegistry

ARTICLE_DIR = Path(__file__).parent / "articles"
NODE_REFERENCE_SLUG = "workflow-steps"
MAX_CHUNK = 1500
_FRONT_MATTER = re.compile(r"^---\n(.*?)\n---\n", re.DOTALL)
_HEADING = re.compile(r"^## (.+)$", re.MULTILINE)
_CATEGORY_TITLES = {
    NodeCategory.TRIGGER: "Triggers (what starts a workflow)",
    NodeCategory.ACTION: "Actions",
    NodeCategory.LOGIC: "Logic and waits",
    NodeCategory.AI: "AI steps",
    NodeCategory.PROTECTION: "Protection",
}


@dataclass(frozen=True)
class Article:
    """One help article."""

    slug: str
    title: str
    category: str
    summary: str
    body: str


@dataclass(frozen=True)
class Chunk:
    """A searchable piece of an article (one section)."""

    key: str
    source: str
    slug: str
    title: str
    heading: str
    content: str

    @property
    def content_hash(self) -> str:
        """Hash of what is embedded (a changed text is embedded again)."""
        text = f"{self.title}\n{self.heading}\n{self.content}"
        return hashlib.sha256(text.encode()).hexdigest()

    @property
    def url(self) -> str:
        """Where the console shows the article."""
        return f"/help/{self.slug}"


def _parse(text: str, fallback_slug: str) -> Article:
    match = _FRONT_MATTER.match(text)
    meta: dict[str, str] = {}
    body = text
    if match:
        for line in match.group(1).splitlines():
            name, _, value = line.partition(":")
            if value:
                meta[name.strip()] = value.strip()
        body = text[match.end() :]
    return Article(
        slug=meta.get("slug", fallback_slug),
        title=meta.get("title", fallback_slug.replace("-", " ").capitalize()),
        category=meta.get("category", "Basics"),
        summary=meta.get("summary", ""),
        body=body.strip(),
    )


@lru_cache(maxsize=1)
def load_articles() -> tuple[Article, ...]:
    """Every shipped help article, sorted by category then title."""
    articles = [
        _parse(path.read_text(encoding="utf-8"), path.stem)
        for path in sorted(ARTICLE_DIR.glob("*.md"))
    ]
    return tuple(sorted(articles, key=lambda item: (item.category, item.title)))


def node_reference(registry: NodeRegistry) -> Article:
    """The workflow steps reference, written from the node registry.

    Args:
        registry: The node types the engine runs.

    Returns:
        An article listing every trigger and step with its outputs and settings.
    """
    lines = [
        "Every trigger and step on the workflow canvas, generated from the engine itself.",
    ]
    by_category: dict[NodeCategory, list[type[BaseNode[Any]]]] = {}
    for node_class in registry.all_nodes():
        by_category.setdefault(node_class.category, []).append(node_class)
    for category, title in _CATEGORY_TITLES.items():
        nodes = by_category.get(category, [])
        if not nodes:
            continue
        lines.append(f"\n## {title}\n")
        for node_class in nodes:
            lines.append(_describe(node_class))
    return Article(
        slug=NODE_REFERENCE_SLUG,
        title="Workflow triggers and steps (reference)",
        category="Automation",
        summary="Every trigger and step, its outputs and its settings.",
        body="\n".join(lines).strip(),
    )


def _describe(node_class: type[BaseNode[Any]]) -> str:
    outputs = ", ".join(node_class.output_handles) if node_class.output_handles else "none (ends)"
    if node_class.has_dynamic_handles:
        outputs = "one per option you configure" + (
            f" (plus {', '.join(node_class.output_handles)})" if node_class.output_handles else ""
        )
    schema = node_class.config_model.model_json_schema()
    required = set(schema.get("required", []))
    fields = []
    for name, spec in (schema.get("properties") or {}).items():
        description = str(spec.get("description", "")).strip()
        marker = " (required)" if name in required else ""
        fields.append(f"`{name}`{marker}" + (f": {description}" if description else ""))
    settings = "; ".join(fields) if fields else "no settings"
    return (
        f"- **{node_class.display_name}** (`{node_class.node_type}`): {node_class.description} "
        f"Outputs: {outputs}. Settings: {settings}."
    )


def chunk_article(article: Article, source: str = "article") -> list[Chunk]:
    """Split an article into its sections (long sections are split on paragraphs).

    Args:
        article: The article.
        source: `article` or `nodes`.

    Returns:
        The chunks, with stable keys `slug#n`.
    """
    parts: list[tuple[str, str]] = []
    matches = list(_HEADING.finditer(article.body))
    intro = article.body[: matches[0].start()] if matches else article.body
    if intro.strip():
        parts.append((article.title, intro.strip()))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(article.body)
        parts.append((match.group(1).strip(), article.body[match.end() : end].strip()))
    chunks: list[Chunk] = []
    for heading, text in parts:
        for piece in _split(text):
            chunks.append(
                Chunk(
                    f"{article.slug}#{len(chunks)}",
                    source,
                    article.slug,
                    article.title,
                    heading,
                    piece,
                )
            )
    return chunks


def _split(text: str) -> list[str]:
    if len(text) <= MAX_CHUNK:
        return [text] if text else []
    pieces: list[str] = []
    current = ""
    for paragraph in re.split(r"\n(?=- |\n)", text):
        if current and len(current) + len(paragraph) > MAX_CHUNK:
            pieces.append(current.strip())
            current = ""
        current += "\n" + paragraph
    if current.strip():
        pieces.append(current.strip())
    return pieces


def all_chunks(registry: NodeRegistry) -> list[Chunk]:
    """Every chunk the assistant can cite: articles plus the node reference."""
    chunks: list[Chunk] = []
    for article in load_articles():
        chunks.extend(chunk_article(article))
    chunks.extend(chunk_article(node_reference(registry), source="nodes"))
    return chunks


def find_article(slug: str, registry: NodeRegistry) -> Article | None:
    """An article by slug (the node reference included)."""
    if slug == NODE_REFERENCE_SLUG:
        return node_reference(registry)
    return next((article for article in load_articles() if article.slug == slug), None)
