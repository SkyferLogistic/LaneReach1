---
slug: quotes
title: Quotes
category: CRM
summary: Build a quotation with charge lines, send it in the thread, let the customer accept it online, reject or re-quote.
---
## Build a quote

Open a deal → **Quotes** → **New quote**. The service line, mode, lane and incoterm come from the deal. Add charges with the preset chips of the mode (for example road freight, fuel surcharge, tolls) or your own lines: quantity × unit price, rounded per line like an invoice. The total updates as you type. Set the validity date (14 days by default), transit time and notes.

**Customer view** shows the quotation exactly as the customer receives it.

**Fill from rate sheets** replaces the lines with the best matching rates from your rate sheets (see *Pricing and rate sheets*).

## Send

**Send** emails the quote in the contact's conversation from their agent with the agent's signature. It passes the guardrails like any email. The deal moves to *Quoted*. A sent quote cannot be edited; **Duplicate** it to re-quote.

## Accept online

The quote email has a **View and accept online** button (and the link in the plain-text version). It opens a page with the quote, its charges and the total, where the customer clicks **Accept this quote** or **Decline**, with their name and an optional note. No sign-in is needed: the link is signed for that one quote, and a changed or made-up link shows "not found". The answer is recorded on the quote ("Online by …" with the note), the deal's owner gets a notification, and everything that follows an accepted or rejected quote happens (the deal's value, workflows on *Quote status changed*). Clicking the same answer twice changes nothing; a quote past its validity date can no longer be accepted online and the page asks the customer to reply for an updated offer. Customers can still reply to the email instead.

## Outcome

- **Accepted** (by you, or by the customer online): sets the deal's value when it had none. Move the deal to Won when booked.
- **Rejected**: recorded on the quote.
- Sent quotes past their validity date **expire** automatically.

Workflows can react: *Quote status changed* (sent, accepted, rejected, expired) and *Quote expiring soon* (1–7 days before validity). *Create quote draft* prepares a quote with the usual charges and empty prices for pricing.

The **Quotes** page lists drafts, sent, expiring soon, accepted, rejected and expired quotes with the win rate. The printable page can be saved as PDF from the browser.
