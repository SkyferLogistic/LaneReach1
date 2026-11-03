"""Versioned prompt templates.

Each prompt is a Markdown file `templates/<task>.<version>.md`:

```
---
task: classify_reply
version: v1
description: ...
---
## system
...
## user
... ${email_content} ...
```

`${name}` placeholders are filled with `string.Template` (values are inserted verbatim and never
re-parsed). Untrusted text must go through `wrap_untrusted`, which puts it inside delimiters the
system prompt declares as data and neutralises any attempt to close the delimiter early.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from string import Template

TEMPLATE_DIR = Path(__file__).parent / "templates"
_FILE_NAME = re.compile(r"^(?P<task>[a-z_]+)\.(?P<version>v\d+)\.md$")
_SECTION = re.compile(r"^## (system|user)\s*$", re.MULTILINE)


class PromptNotFoundError(LookupError):
    """No prompt file exists for the requested task/version."""


@dataclass(frozen=True)
class PromptTemplate:
    """A parsed prompt file."""

    task: str
    version: str
    description: str
    system: str
    user: str

    def render(self, **values: str) -> RenderedPrompt:
        """Fill the placeholders.

        Args:
            **values: Placeholder values (untrusted ones already wrapped).

        Returns:
            The rendered prompt.

        Raises:
            KeyError: If a placeholder has no value.
        """
        return RenderedPrompt(
            task=self.task,
            version=self.version,
            system=Template(self.system).substitute(values),
            user=Template(self.user).substitute(values),
        )


@dataclass(frozen=True)
class RenderedPrompt:
    """A prompt ready to send."""

    task: str
    version: str
    system: str
    user: str


def wrap_untrusted(text: str, tag: str = "email_content") -> str:
    """Wrap untrusted content in delimiters.

    A closing tag inside the content is defused so the content cannot end the data block and
    pretend to be instructions.

    Args:
        text: Untrusted text (an email, a template being translated).
        tag: Delimiter tag name.

    Returns:
        The wrapped text.
    """
    defused = re.sub(rf"</?\s*{tag}\s*>", f"[{tag} tag removed]", text, flags=re.IGNORECASE)
    return f"<{tag}>\n{defused}\n</{tag}>"


def _parse(path: Path) -> PromptTemplate:
    raw = path.read_text(encoding="utf-8")
    if not raw.startswith("---"):
        raise ValueError(f"{path.name}: missing front-matter")
    _, front, body = raw.split("---", 2)
    meta = dict(
        (key.strip(), value.strip())
        for key, value in (line.split(":", 1) for line in front.strip().splitlines() if ":" in line)
    )
    parts = _SECTION.split(body)
    sections = {parts[index]: parts[index + 1].strip() for index in range(1, len(parts) - 1, 2)}
    if set(sections) != {"system", "user"}:
        raise ValueError(f"{path.name}: needs '## system' and '## user' sections")
    match = _FILE_NAME.match(path.name)
    if (
        match is None
        or meta.get("task") != match["task"]
        or meta.get("version") != match["version"]
    ):
        raise ValueError(f"{path.name}: front-matter does not match the file name")
    return PromptTemplate(
        task=meta["task"],
        version=meta["version"],
        description=meta.get("description", ""),
        system=sections["system"],
        user=sections["user"],
    )


@lru_cache(maxsize=1)
def load_prompts() -> dict[tuple[str, str], PromptTemplate]:
    """Load and validate every prompt file (cached).

    Returns:
        Prompts keyed by (task, version).
    """
    prompts: dict[tuple[str, str], PromptTemplate] = {}
    for path in sorted(TEMPLATE_DIR.glob("*.md")):
        prompt = _parse(path)
        prompts[(prompt.task, prompt.version)] = prompt
    return prompts


def get_prompt(task: str, version: str = "latest") -> PromptTemplate:
    """Return a prompt template.

    Args:
        task: Task name, for example `classify_reply`.
        version: `v1`, `v2`, ... or `latest` (highest version number).

    Returns:
        The template.

    Raises:
        PromptNotFoundError: If no matching prompt exists.
    """
    prompts = load_prompts()
    if version == "latest":
        versions = sorted(
            (int(prompt_version[1:]), prompt_version)
            for prompt_task, prompt_version in prompts
            if prompt_task == task
        )
        if not versions:
            raise PromptNotFoundError(task)
        version = versions[-1][1]
    try:
        return prompts[(task, version)]
    except KeyError as exc:
        raise PromptNotFoundError(f"{task}.{version}") from exc
