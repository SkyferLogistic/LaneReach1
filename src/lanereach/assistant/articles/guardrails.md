---
slug: guardrails
title: Guardrails: what is checked before every email
category: Automation
summary: The rules every email passes, in order, and what each does.
---
Guardrails live in the engine, not in workflows or AI: **the AI never decides whether an email may be sent.** Each email is checked in this order; the first rule that blocks or defers decides, and the decision is shown on the enrollment step.

1. **Kill switch**: sending paused for the whole company or one office. Checked again every 15 minutes.
2. **Suppression**: the address or domain is on the do-not-contact list (unsubscribes, complaints, hard bounces).
3. **Email verification**: the address was verified as invalid.
4. **Company freeze**: the company was frozen (for example after a complaint).
5. **Frequency cap**: the contact received a cold email in the last 30 days.
6. **Company rotation**: only one office may start outreach to a company per week.
7. **Campaign schedule**: the workflow's own days and hours.
8. **Business days**: weekends and public holidays of the recipient's country.
9. **Sending window**: only between 09:00 and 11:30 recipient time by default (Settings → Sending).
10. **Mailbox connected**: with real sending, the agent needs a connected, tested mailbox.
11. **Mailbox capacity**: the agent's daily cap.

Deferred emails wait and are checked again in full when they wake. Replies a person approved skip the timing rules but still respect the kill switch, suppressions, freezes and capacity.
