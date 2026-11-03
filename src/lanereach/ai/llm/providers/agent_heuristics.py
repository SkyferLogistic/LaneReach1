"""Mock data assistant: picks a tool from keywords, then lists what the tool returned.

Deterministic, so tests and key-less development exercise the real tool loop: first step a tool
chosen from the question, second step an answer built only from the tool results.
"""

from __future__ import annotations

import json
import re
from typing import Any

_QUESTION = re.compile(r"<question>\n(.*?)\n</question>", re.DOTALL)
_RESULTS = re.compile(r"<tool_results>\n(.*?)\n</tool_results>", re.DOTALL)
_TOOLS = re.compile(r"Tools available now:\n(.*?)\n\n", re.DOTALL)
_LANE = re.compile(
    r"\bfrom\s+([A-Za-z .'-]{2,40}?)\s+to\s+([A-Za-z .'-]{2,40}?)"
    r"(?:[?.!,]|\s+(?:for|by|in|on)\b|$)",
    re.IGNORECASE,
)
_NOUNS = {
    "find_deals": "deal",
    "find_quotes": "quote",
    "find_contacts": "contact",
    "find_rates": "rate option",
    "inbox_waiting": "conversation waiting for an answer",
    "search_help": "help section",
}


def _choose_tool(question: str, offered: set[str]) -> tuple[str, dict[str, Any]]:
    text = question.lower()
    lane = _LANE.search(question)
    lane_args = (
        {"origin": lane.group(1).strip(), "destination": lane.group(2).strip()} if lane else {}
    )
    candidates: list[tuple[str, dict[str, Any]]] = []
    if re.search(r"\bhow (do|can|to)\b|\bwhere (do|can)\b|\bcan lanereach\b", text):
        candidates.append(("search_help", {"query": question[:200]}))
    if re.search(r"\brates?\b|\bprice\b|\bcost\b|\btariff", text) and lane_args:
        candidates.append(("find_rates", lane_args))
    if "quote" in text:
        args: dict[str, Any] = dict(lane_args)
        if re.search(r"expir", text):
            args["within_days"] = 7
        elif re.search(r"\bsent\b|\bwaiting\b|\bpending\b|\bopen\b", text):
            args["status"] = "sent"
        candidates.append(("find_quotes", args))
    if re.search(
        r"\binbox\b|unanswered|waiting for (a |our )?(reply|answer)|need(s)? an answer", text
    ):
        candidates.append(("inbox_waiting", {}))
    if re.search(
        r"how many|this week|last week|this month|summary|activity|how (are|did) we", text
    ):
        candidates.append(("activity_summary", {"within_days": 30 if "month" in text else 7}))
    if re.search(r"\bcontact\b|\bwho is\b|\bemail of\b", text):
        names = re.findall(r"\b[A-Z][a-z]{2,}\b", question)
        candidates.append(("find_contacts", {"query": names[-1]} if names else {}))
    deal_args: dict[str, Any] = dict(lane_args)
    if "hot" in text:
        deal_args["min_score"] = 70
    if "quiet" in text or "stuck" in text:
        deal_args["quiet_days"] = 5
    if re.search(r"\bwon\b", text):
        deal_args["status"] = "won"
    elif re.search(r"\blost\b", text):
        deal_args["status"] = "lost"
    candidates.append(("find_deals", deal_args))
    for tool, args in candidates:
        if tool in offered:
            return tool, args
    return next(iter(sorted(offered))), {}


def _answer(results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return {"action": "answer", "answer": "I could not look this up.", "record_refs": []}
    last = results[-1]
    if "counts" in last:
        counts = last["counts"]
        lines = [
            f"- {key.replace('_', ' ')}: {value}" for key, value in counts.items() if key != "days"
        ]
        text = f"In the last {counts.get('days', 7)} days:\n" + "\n".join(lines)
        return {"action": "answer", "answer": text, "record_refs": []}
    items = last.get("items") or []
    noun = _NOUNS.get(str(last.get("tool")), "record")
    count = int(last.get("count") or 0)
    if not items:
        return {"action": "answer", "answer": f"I found no {noun}s for this.", "record_refs": []}
    lines = []
    for item in items[:5]:
        label = item.get("title") or item.get("name") or item.get("subject") or item.get("sheet")
        facts = [
            str(item[key])
            for key in ("score", "lane", "total", "status", "stage", "company", "days_waiting")
            if item.get(key) not in (None, "")
        ]
        lines.append(f"- {label}" + (f" ({', '.join(facts)})" if facts else ""))
    plural = noun if count == 1 else f"{noun}s"
    text = f"I found {count} {plural}:\n" + "\n".join(lines)
    return {
        "action": "answer",
        "answer": text,
        "record_refs": [str(item["ref"]) for item in items[:5] if item.get("ref")],
    }


def answer_data_question_heuristically(prompt: str) -> dict[str, Any]:
    """One step of the data assistant."""
    question_match = _QUESTION.search(prompt)
    question = question_match.group(1).strip() if question_match else ""
    results_match = _RESULTS.search(prompt)
    try:
        results = json.loads(results_match.group(1)) if results_match else []
    except json.JSONDecodeError:
        results = []
    tools_match = _TOOLS.search(prompt)
    offered = (
        set(re.findall(r"^- ([a-z_]+):", tools_match.group(1), re.MULTILINE))
        if tools_match
        else set()
    )
    if results or not offered:
        return _answer(results)
    tool, arguments = _choose_tool(question, offered)
    return {"action": "use_tool", "tool": tool, "arguments": arguments, "record_refs": []}
