---
slug: ai-usage
title: AI usage, budget and built-in messages
category: Settings
summary: Set a monthly AI budget, choose AI or rules per area, answer some replies with built-in messages, and see this month's AI cost by task.
---
## Where AI is used

LaneReach uses AI to read replies (what kind of reply it is, the shipment details, buying intent), to write (reply drafts, follow-ups for quiet deals, the weekly digest, quote cover notes, workflow drafts) and to answer questions in the assistant. Bounces, out-of-office replies, unsubscribes and complaints are recognised by rules first, without any AI call.

Open **Settings → AI usage** to see this month's estimated AI cost, the budget, which model runs the reading and the writing tasks, and the cost of each task.

## Monthly AI budget

An admin can set a **monthly AI budget** in US dollars. When this month's estimated cost reaches it, LaneReach calls no model until the next month or until the budget is raised. Nothing stops working: replies are still read by rules and go to a person, each reply type gets its built-in message, lead scores use rules and the weekly digest shows the numbers. People who click an AI button see "Monthly AI budget used up".

A budget of 0 turns AI off. Costs are estimates from the model prices your administrator configured; without prices every cost shows as $0.00 and a budget never fills up.

## AI or rules, per area

- **Lead scores**: *AI reading* reads buying intent in each new deal's reply; *Rules only* scores from the shipment details, reply speed, history and quotes.
- **Quiet-deal follow-ups**: *AI writes* each follow-up from the conversation; *Built-in message* uses your follow-up message (template key `follow_up_quiet`, else LaneReach's own text) and suggests closing a deal quiet for 30 days with no quote waiting.
- **Weekly digest**: *AI summary* or *Numbers only*.

## Built-in messages for replies

In **Settings → Reply policy**, the **Written by** column chooses, for each kind of reply that gets a drafted answer, an **AI draft** or a **Built-in message**. A built-in message is your template with the key `reply_<kind>` (for example `reply_meeting_request` or `reply_rate_query`) in the contact's language, else the office's language, else English; without a template LaneReach uses its own short English text. It costs no AI call and says the same thing every time, which suits meeting requests, referrals and the first answer to a rate request while pricing prepares the quote.

Built-in drafts go to the review queue like AI drafts (marked "built-in message") unless you enabled auto-send for that kind of reply. When an AI draft cannot be written (budget used up, AI unavailable) and the reply was understood with confidence, the built-in message stands in; it is never auto-sent.

Placeholders you can use in these messages: `{{contact.first_name|there}}`, `{{contact.full_name}}`, `{{company.name}}`, `{{office.name}}`, `{{office.owner_name}}`, `{{sender.name}}` and `{{sender.first_name}}`; follow-ups also have `{{deal.lane|your shipment}}`.

## A cheaper model for reading

Your administrator can set a second, cheaper model for the reading tasks (sorting replies, shipment details, buying intent, summaries and rate-lane matching). The writing tasks keep the main model. The usage table shows which model each task used.
