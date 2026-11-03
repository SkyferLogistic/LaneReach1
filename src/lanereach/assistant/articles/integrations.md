---
slug: integrations
title: Integrations
category: Tools
summary: Webhooks, Slack, Calendly and Cal.com, email verification.
---
Open **Integrations** to connect:

- **Signed webhooks** for Zapier, Make, n8n or any system: choose events such as `lead.created`, `deal.won`, `meeting.booked`. Deliveries are signed (HMAC), retried and never sent to private or internal addresses.
- **Slack**: an incoming-webhook URL per channel for alerts (new leads, won deals) and the *Notify Slack* workflow step.
- **Calendly / Cal.com**: bookings and cancellations become meetings on the contact and deal and can start workflows (*Meeting booked*, *Meeting canceled*).
- **MillionVerifier**: email verification with an API key; without it, offline rules are used.

Secrets are stored encrypted and never shown again after saving.
