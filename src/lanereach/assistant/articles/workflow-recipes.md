---
slug: workflow-recipes
title: Workflow recipes
category: Automation
summary: Ready-made workflows for common freight outreach and sales situations.
---
Start any of these from **Workflows → New workflow**:

- **Intro sequence**: tag `intro` → intro email → wait 5 business days → follow-up → stop. A real reply stops the sequence automatically.
- **Air / ocean script sequence**: the script in the contact's language from agents of that line, a wait and a follow-up.
- **Route by region**: *Switch on field* on the contact's country: Gulf, Europe and the rest each get their own email.
- **Rate request handover**: reply classified as a rate query → extract the shipment → tell the office owner → create a quote request for pricing.
- **Won deal → customer**: deal won → tag customer → stage customer → tell the office owner.
- **Lost deal → try again later**: deal lost → wait about three months → call task → friendly follow-up.
- **Quote expiring → chase**: 3 days before a sent quote expires → reminder email in the thread → call task.
- **Stale deal nudge**: a deal has not moved for 7 days → high-priority task for the owner → note on the deal.
- **Bounce handling**: an email hard-bounces → task to find another contact at the company → tell the office owner.
- **New quote request → draft quote**: a quote request arrives → draft quote with the usual charges of its mode → pricing task.
- **Website quote request**: your website sends a `quote_requested` event → pricing lead → tell the owner.

You can also ask the assistant to draft a workflow from a description; it is saved as a draft for you to check.
