"""Mock heuristics for `assess_lead`, `write_digest` and `suggest_follow_up` (tests, no-key dev)."""

from __future__ import annotations

import json
import re
from typing import Any

_EMAIL = re.compile(r"<email_content>\s*(.*?)\s*</email_content>", re.DOTALL)
_STATS = re.compile(r"<digest_stats>\s*(.*?)\s*</digest_stats>", re.DOTALL)
_CONVERSATION = re.compile(r"<conversation>\s*(.*?)\s*</conversation>", re.DOTALL)
_DEAL = re.compile(r"Deal: (.*)")
_FACTS = re.compile(r"<quote_facts>\s*(.*?)\s*</quote_facts>", re.DOTALL)
_TURN = re.compile(
    r"\[(us|them) · ([0-9-]+)\] Subject: (.*?)\n(.*?)(?=\n\n\[(?:us|them) · |\Z)", re.DOTALL
)

_SIGNALS: list[tuple[str, str, float]] = [
    (r"\bquote|rate|price|pricing|cotizaci|teklif|angebot", "asks for a quote", 0.25),
    (
        r"\b\d+\s*x\s*(20|40|45)|\b(20|40|45)\s*(gp|hc|hq|rf|')",
        "gives container count and size",
        0.15,
    ),
    (r"\bfrom\b.+\bto\b|→|->", "names a full lane", 0.1),
    (
        r"\bready\b|\bcargo ready\b|\bnext week\b|\bthis week\b|\btomorrow\b",
        "cargo ready date given",
        0.1,
    ),
    (
        r"\bweekly\b|\bmonthly\b|\bregular\b|\bevery (week|month)\b|\bper month\b",
        "regular volumes",
        0.15,
    ),
    (r"\burgent\b|\basap\b|\bas soon as\b", "urgent", 0.1),
    (r"\bbook\b|\bbooking\b|\bconfirm\b", "asks how to book", 0.1),
]
_CONCERNS: list[tuple[str, str, float]] = [
    (r"\bexpensive\b|\bcheaper\b|\bbudget\b|\bbest price\b", "price sensitive", 0.1),
    (
        r"\balready (have|work)|\bcurrent forwarder\b|\bexisting forwarder\b",
        "already has a forwarder",
        0.25,
    ),
    (r"\bnot (now|interested)\b|\bmaybe later\b|\bnext year\b", "not ready now", 0.3),
]


def _count(count: Any, word: str, many: str | None = None) -> str:
    """`1 deal`, `3 deals` (the mock's text should read like the real thing)."""
    return f"{count} {word if count == 1 else many or word + 's'}"


def assess_lead_heuristically(prompt: str) -> dict[str, Any]:
    """Keyword-based intent."""
    match = _EMAIL.search(prompt)
    text = (match.group(1) if match else prompt).lower()
    intent = 0.25
    signals: list[str] = []
    concerns: list[str] = []
    for pattern, label, weight in _SIGNALS:
        if re.search(pattern, text):
            intent += weight
            signals.append(label)
    for pattern, label, weight in _CONCERNS:
        if re.search(pattern, text):
            intent -= weight
            concerns.append(label)
    urgency = "high" if "urgent" in signals or "cargo ready date given" in signals else "normal"
    if "not ready now" in concerns:
        urgency = "low"
    return {
        "intent": round(max(0.0, min(1.0, intent)), 2),
        "urgency": urgency,
        "buying_signals": signals[:5],
        "concerns": concerns[:3],
        "summary": "Keyword reading of the latest reply.",
    }


def write_digest_heuristically(prompt: str) -> dict[str, Any]:
    """A digest assembled from the stats, quoting their numbers as they are."""
    match = _STATS.search(prompt)
    try:
        stats: dict[str, Any] = json.loads(match.group(1)) if match else {}
    except ValueError:
        stats = {}
    highlights: list[dict[str, str]] = []
    requests = stats.get("quote_requests") or {}
    lanes = stats.get("lanes_heating_up") or []
    for lane in lanes[:2]:
        highlights.append(
            {
                "kind": "lanes",
                "title": f"{lane['lane']} is heating up",
                "detail": f"{lane['this_week']} quote requests this week against an average of "
                f"{lane['previous_4_week_average']} a week before. "
                "Check the lane is in a rate sheet.",
            }
        )
    for row in (stats.get("win_loss_by_lane") or [])[:1]:
        highlights.append(
            {
                "kind": "win_loss",
                "title": f"{row['lane']}: {row['win_rate_percent']}% won",
                "detail": f"{row['won']} won and {row['lost']} lost in the last 90 days.",
            }
        )
    waiting = stats.get("waiting_for_quote") or {}
    if waiting.get("deals"):
        highlights.append(
            {
                "kind": "speed",
                "title": f"{_count(waiting['deals'], 'deal')} waiting for a quote",
                "detail": f"The oldest has waited {_count(waiting['oldest_days'], 'day')}. "
                "Price these first.",
            }
        )
    quotes = stats.get("quotes") or {}
    if quotes.get("sent"):
        highlights.append(
            {
                "kind": "quotes",
                "title": f"{_count(quotes['sent'], 'quote')} sent",
                "detail": f"{quotes.get('accepted', 0)} accepted and "
                f"{quotes.get('rejected', 0)} rejected this week.",
            }
        )
    replies = stats.get("replies") or {}
    if replies.get("total"):
        highlights.append(
            {
                "kind": "replies",
                "title": f"{_count(replies['total'], 'reply', 'replies')} read",
                "detail": "Every reply was sorted; rate requests went to pricing.",
            }
        )
    if lanes:
        top = lanes[0]
        headline = f"{top['lane']} is heating up with {top['this_week']} quote requests this week."
    elif requests.get("this_week"):
        count = requests["this_week"]
        headline = f"{count} quote request{'' if count == 1 else 's'} came in this week."
    else:
        headline = "A quiet week: no new quote requests."
    summary = (
        f"{requests.get('this_week', 0)} quote requests this week against an average of "
        f"{requests.get('previous_4_week_average', 0)} a week over the previous four weeks."
    )
    return {"headline": headline, "summary": summary, "highlights": highlights[:6]}


def suggest_follow_up_heuristically(prompt: str) -> dict[str, Any]:
    """Email the customer unless the conversation shows they declined or asked to wait."""
    deal = _DEAL.search(prompt)
    context = deal.group(1) if deal else ""
    match = _CONVERSATION.search(prompt)
    conversation = (match.group(1) if match else "").lower()
    lane = re.search(r"lane ([^;]+)", context)
    lane_text = lane.group(1).strip() if lane else "your shipment"
    if re.search(r"not interested|went with another|chose another|no longer", conversation):
        return {
            "action": "close_lost",
            "reason": "The customer said they went elsewhere.",
            "subject": "",
            "body": "",
            "language": "en",
        }
    if re.search(r"next month|next quarter|after (the )?holiday|in a few weeks", conversation):
        return {
            "action": "wait",
            "reason": "The customer asked to be contacted later.",
            "subject": "",
            "body": "",
            "language": "en",
        }
    return {
        "action": "email",
        "reason": "No answer since the last message; a short check-in keeps the deal alive.",
        "subject": f"Following up: {lane_text}",
        "body": (
            "Hello,\n\nI wanted to follow up on our last message about "
            f"{lane_text}. Is the shipment still planned, and is there anything we can clarify "
            "for you?\n\nBest regards"
        ),
        "language": "en",
    }


def summarize_thread_heuristically(prompt: str) -> dict[str, Any]:
    """The prospect's last message, their questions and a next step from who wrote last."""
    match = _CONVERSATION.search(prompt)
    turns = _TURN.findall(match.group(1) if match else "")
    theirs = [body.strip() for direction, _day, _subject, body in turns if direction == "them"]
    last_theirs = theirs[-1] if theirs else ""
    sentences = [part.strip() for part in re.split(r"(?<=[.!?])\s+", last_theirs) if part.strip()]
    wants = [
        label for pattern, label, _weight in _SIGNALS if re.search(pattern, last_theirs.lower())
    ]
    questions = [sentence for sentence in sentences if sentence.endswith("?")][:4]
    last_direction = turns[-1][0] if turns else "us"
    next_step = (
        "Answer their latest message; pricing may need to quote."
        if last_direction == "them"
        else "Wait for their answer, then follow up if they stay quiet."
    )
    lowered = last_theirs.lower()
    sentiment = (
        "negative"
        if re.search(r"not interested|remove me|stop|complain", lowered)
        else (
            "positive" if re.search(r"thank|great|please quote|interested", lowered) else "neutral"
        )
    )
    summary = (
        f"{_count(len(turns), 'message')}; the prospect last wrote: {sentences[0]}"[:480]
        if sentences
        else f"{_count(len(turns), 'message')}, no reply from the prospect yet."
    )
    return {
        "summary": summary,
        "customer_wants": wants[:5],
        "open_questions": questions,
        "next_step": next_step,
        "sentiment": sentiment,
        "language": "en",
    }


def draft_quote_note_heuristically(prompt: str) -> dict[str, Any]:
    """A cover note built from the quote facts only."""
    match = _FACTS.search(prompt)
    try:
        facts: dict[str, Any] = json.loads(match.group(1)) if match else {}
    except ValueError:
        facts = {}
    first = facts.get("contact_first_name") or "there"
    lane = f"from {facts.get('origin') or '?'} to {facts.get('destination') or '?'}"
    text = (
        f"Hello {first},\n\nThank you for your request. Please find below our offer for "
        f"{facts.get('service') or 'your shipment'} {lane}: {facts.get('total')} "
        f"{facts.get('currency')} in total, valid until {facts.get('valid_until')}."
    )
    if facts.get("transit_days"):
        text += f" The transit time is about {_count(facts['transit_days'], 'day')}."
    text += "\n\nPlease let us know if you have any questions."
    return {"intro": text, "language": facts.get("language") or "en"}
