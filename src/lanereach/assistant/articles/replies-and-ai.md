---
slug: replies-and-ai
title: Replies, AI classification and the review queue
category: AI
summary: How replies are read, what the AI does, and what needs a person.
---
## What happens to a reply

1. The reply is matched to the conversation it answers.
2. Rules and then the AI **classify** it: interested, meeting request, rate query, service query, partnership, referral, wrong office, later, not interested, complaint, existing partner, unsubscribe, out of office, auto-reply, bounce or unclear.
3. **Outreach stops** on a real reply (not on auto-replies or out-of-office).
4. The reply policy decides what happens next, for example:
   - rate or service query → the shipment details are extracted (origin, destination, mode, weight, incoterm…) and a **quote request** and deal are created for pricing; an answer draft goes to the review queue;
   - interested or meeting request → a draft for review and the office owner is told;
   - unsubscribe or complaint → the contact becomes *do not contact*; complaints also freeze the company;
   - out of office → the next email is rescheduled after the return date;
   - hard bounce → the contact is suppressed.
5. A `reply.classified` event starts workflows that listen to it.

## The review queue

AI drafts wait in the **review queue**: read the reply and the AI's reasoning, edit the draft, then approve & send in the thread, or reject. When the AI is not confident enough (below 80 %), a person always reviews. Email content is treated as untrusted data: instructions inside an email are never followed.

Admins can change the policy per reply type in **Settings → Reply policy**.

## Drafts that sound like your team

Before the AI drafts an answer, LaneReach looks in your own history for earlier replies of the same kind with the closest words, and the answer your office actually sent. Up to two of them go to the AI as examples of your tone and of what you say about your services; the AI is told never to copy names, prices, dates or lanes from them. Drafts written this way show "based on N of your past answers" in the review queue. Only your company's messages are searched, and emails whose text was removed by retention are not used. The search needs no extra AI call.
