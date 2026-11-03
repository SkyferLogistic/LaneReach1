---
slug: ai-insights
title: AI insights, lead scores and follow-ups
category: CRM
summary: Lead scores with reasons, a weekly AI digest, follow-ups for quiet deals, conversation summaries, AI cover notes for quotes and the AI's track record.
---
## Lead scores

Every open deal gets a lead score from 0 to 100, shown on the pipeline card and in the deal's **AI** tab with every reason and its points. Hot is 70 and above, warm 45 to 69, cold below 45.

When a rate request arrives, the AI reads the reply for buying intent and urgency (for example a full lane with container count, a ready date, regular volumes, or concerns like "already has a forwarder"). The other points are facts from LaneReach: how complete the shipment details are, how fast they replied, how many replies, regular shipments, the company's won and lost deals, quotes sent, accepted or rejected, and days without activity.

Scores are recounted every night without calling the AI again. **Rescore with AI** in the AI tab reads the latest reply again. Scores only order your work list: they never send an email or move a deal.

## Weekly digest

Every Monday LaneReach writes a digest of the week before, shown on **AI insights** with an in-app notification. The numbers are computed by LaneReach: quote requests and the lanes heating up against the previous four weeks, win and loss by lane over 90 days, replies by kind, quotes sent and decided, deals waiting for a quote by office, and quiet deals. The AI writes the headline, summary and highlights from those numbers only: an answer that uses any number that is not in the week's statistics is rejected, and the digest is then written by rules instead (marked *Written by rules*). **Write this week's digest** builds one for the current week so far.

## Follow-ups for quiet deals

A deal is quiet when nothing happened on it (no email with the contact, no stage move, no edit) for 5 days (`QUIET_DEAL_DAYS`). Every morning the AI suggests one next step for quiet deals (send a follow-up email, call, wait because they asked for later, or close as lost) and drafts the message in the customer's language. You see them on **AI insights** and in the deal's AI tab.

**Make it a task** creates a task for the deal's owner with the draft in its notes; **Copy draft** copies it; **Dismiss** removes it. LaneReach never sends a suggestion by itself. You can also ask for a suggestion on any open deal with **Suggest a follow-up**.

## Conversation summaries

In the **Inbox**, **Summarize with AI** reads the conversation and shows where it stands, what the prospect asked for, the questions still to answer and the next step. The summary is shown on screen only; nothing is stored or sent.

## Cover notes for quotes

In the quote builder, **Write with AI** next to *Message above the quote* drafts a short note in the contact's language from the quote itself: the service and lane, the total, the validity date and the transit time. It may only use numbers that are in the quote, never links; edit it before you send the quote.

## AI quality

The **AI quality** card on **AI insights** shows how the AI did over the last 30 days, measured from what people did with its work (no AI call): how many replies were sorted by rules, by the AI or sent to a person; how many AI drafts and built-in messages went out as written, were edited or rejected; how many follow-up suggestions were used; the average lead score of won deals against lost deals (a useful score is higher for won deals); and how many AI runs failed.
