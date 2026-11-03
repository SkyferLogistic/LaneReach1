"""Deterministic stand-ins for the assistant's AI tasks (tests and development without a key).

* Answers are extractive: the best-matching help section, quoted, never invented.
* Workflow drafts follow simple keyword rules (tag, wait N days, send, task, notify...).
"""

from __future__ import annotations

import json
import re
from itertools import pairwise
from typing import Any

_QUESTION = re.compile(r"<question>\s*(.*?)\s*</question>", re.DOTALL)
_ARTICLES = re.compile(r"<help_articles>\s*(.*?)\s*</help_articles>", re.DOTALL)
_DESCRIPTION = re.compile(r"<description>\s*(.*?)\s*</description>", re.DOTALL)
_WORKSPACE = re.compile(r"<workspace>\s*(.*?)\s*</workspace>", re.DOTALL)
_WORD = re.compile(r"[a-z0-9]+")
_STOP = frozenset(
    [
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "can",
        "could",
        "do",
        "does",
        "for",
        "from",
        "get",
        "have",
        "how",
        "i",
        "if",
        "in",
        "is",
        "it",
        "its",
        "me",
        "my",
        "of",
        "on",
        "or",
        "please",
        "should",
        "so",
        "that",
        "the",
        "their",
        "them",
        "then",
        "there",
        "this",
        "to",
        "up",
        "use",
        "using",
        "want",
        "what",
        "when",
        "where",
        "which",
        "who",
        "why",
        "will",
        "with",
        "would",
        "you",
        "your",
        "lanereach",
    ]
)
_TAG = re.compile(r"tag(?:ged)?\s+(?:as\s+|with\s+)?[\"'`]?([a-z0-9][a-z0-9_-]{0,40})")
_WAIT = re.compile(r"wait(?:s|ing)?\s+(?:for\s+)?(\d{1,2})\s*(?:business\s+|working\s+)?days?")


def _words(text: str) -> set[str]:
    return {word for word in _WORD.findall(text.lower()) if word not in _STOP and len(word) > 2}


def _json_block(pattern: re.Pattern[str], text: str) -> Any:
    match = pattern.search(text)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except ValueError:
        return None


def answer_heuristically(prompt: str) -> dict[str, Any]:
    """Quote the help section sharing the most words with the question."""
    question_match = _QUESTION.search(prompt)
    question = _words(question_match.group(1) if question_match else "")
    articles = _json_block(_ARTICLES, prompt) or []
    scored = []
    for article in articles if isinstance(articles, list) else []:
        text = f"{article.get('title', '')} {article.get('heading', '')} {article.get('text', '')}"
        scored.append((len(question & _words(text)), article))
    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored or scored[0][0] == 0:
        return {
            "answer": "I could not find this in the LaneReach help articles, so LaneReach may "
            "not do it. Try other words, or browse the Help center.",
            "source_ids": [],
            "supported": False,
            "confidence": 0.2,
        }
    best = scored[0][1]
    excerpt = str(best.get("text", "")).strip()
    if len(excerpt) > 700:
        cut = excerpt.rfind(". ", 0, 700)
        excerpt = excerpt[: cut + 1 if cut > 200 else 700].rstrip() + " …"
    sources = [str(item[1].get("id")) for item in scored[:2] if item[0] > 0]
    title, heading = str(best.get("title", "")), str(best.get("heading", ""))
    place = title if heading in ("", title) else f"{title}: {heading}"
    return {
        "answer": f"From “{place}”:\n\n{excerpt}",
        "source_ids": sources,
        "supported": best.get("slug") != "limitations",
        "confidence": 0.5,
    }


def _trigger(text: str) -> tuple[str, dict[str, Any]]:
    tag = _TAG.search(text)
    if "expir" in text and "quote" in text:
        return "trigger_quote_expiring", {"days_before": 3}
    if "bounce" in text:
        return "trigger_email_bounced", {"bounce_types": ["hard"]}
    if "form" in text:
        return "trigger_form_submitted", {}
    if "repl" in text and ("rate" in text or "price" in text or "quote" in text):
        return "trigger_reply_classified", {"buckets": ["rate_query"]}
    if "repl" in text:
        return "trigger_reply_received", {}
    if "deal" in text and "won" in text:
        return "trigger_deal_stage_changed", {"kinds": ["won"]}
    if tag and text.find("tag") < 40:
        return "trigger_tag_added", {"tag": tag.group(1)}
    if "new contact" in text or "contact is created" in text or "contact created" in text:
        return "trigger_contact_created", {}
    return "trigger_manual_enroll", {}


def draft_workflow_heuristically(prompt: str) -> dict[str, Any]:
    """A linear workflow following the description's clauses in order."""
    match = _DESCRIPTION.search(prompt)
    text = (match.group(1) if match else "").lower()
    workspace = _json_block(_WORKSPACE, prompt) or {}
    offices = workspace.get("offices") or []
    templates = [item.get("key") for item in workspace.get("templates") or [] if item.get("key")]
    trigger_type, trigger_config = _trigger(text)
    steps: list[dict[str, Any]] = []
    assumptions: list[str] = []
    last_was_send = False
    tag_used_by_trigger = trigger_type == "trigger_tag_added"

    def add(step_type: str, config: dict[str, Any]) -> None:
        steps.append(
            {
                "id": f"{step_type}_{len(steps) + 1}",
                "type": step_type,
                "config_json": json.dumps(config),
            }
        )

    for clause in re.split(r"\bthen\b|,|;|\band\b|\.", text):
        clause = clause.strip()
        if not clause:
            continue
        wait = _WAIT.search(clause)
        if wait:
            add("wait_business_days", {"days": max(1, min(int(wait.group(1)), 60))})
            last_was_send = False
        elif any(word in clause for word in ("send", "email", "follow-up", "follow up", "intro")):
            if not offices or not templates:
                assumptions.append(
                    "No office or email template exists yet, so the email step was left out."
                )
                continue
            if last_was_send:
                add("wait_business_days", {"days": 3})
                assumptions.append("Added a 3-business-day wait between two emails (required).")
            follow = next((key for key in templates if "follow" in key), None)
            intro = next((key for key in templates if "intro" in key), None)
            if "follow" in clause and follow:
                template = follow
            elif "intro" in clause and intro:
                template = intro
            else:
                template = next((key for key in templates if key != follow), templates[0])
            add("send_email", {"office_id": offices[0]["id"], "template_key": template})
            last_was_send = True
        elif "task" in clause or "call" in clause:
            add(
                "create_task",
                {
                    "title": "Follow up with {{contact.full_name}}",
                    "assignee": "office_owner",
                    "due_in_business_days": 1,
                },
            )
            last_was_send = False
        elif any(word in clause for word in ("notify", "tell", "alert")):
            add(
                "notify_office_owner",
                {"message": "{{contact.full_name}} at {{company.name}} needs attention."},
            )
            last_was_send = False
        elif "quote" in clause and "draft" in clause:
            add("create_quote_draft", {"validity_days": 14})
            last_was_send = False
        elif "tag" in clause and not tag_used_by_trigger:
            tag = _TAG.search(clause)
            if tag:
                add("add_tag", {"tag": tag.group(1)})
                last_was_send = False
        tag_used_by_trigger = False if steps else tag_used_by_trigger
    add("stop", {})
    ids = ["trigger", *[step["id"] for step in steps]]
    edges = [
        {"source": source, "target": target, "handle": "next"} for source, target in pairwise(ids)
    ]
    described = " ".join((match.group(1) if match else "").split()).rstrip(".")
    name = (described[:1].upper() + described[1:60]).strip()
    return {
        "name": name if len(name) >= 3 else "Drafted workflow",
        "summary": "A draft built from your description; check each step before publishing.",
        "trigger": {
            "id": "trigger",
            "type": trigger_type,
            "config_json": json.dumps(trigger_config),
        },
        "steps": steps,
        "edges": edges,
        "assumptions": assumptions[:8],
    }
