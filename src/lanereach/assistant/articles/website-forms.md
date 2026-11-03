---
slug: website-forms
title: Website forms
category: Tools
summary: "Request a quote" forms on your website that create contacts and start workflows.
---
On **Website forms**, build a form (for example *Request a quote* with origin, destination, mode and message) and copy the HTML into your website: no script is needed.

- A submission creates or updates the contact with a tag and office, and publishes `form.submitted`: the *Web form submitted* trigger turns the answers into workflow variables.
- Spam protection: a hidden honeypot field and rate limits (at most 5 submissions per address per hour and 30 per form per minute). LaneReach does not use or solve CAPTCHAs.
- Submissions are listed per form.

## The LaneReach website's Contact us

The public LaneReach website (the page visitors see before signing in) sends *Contact us* to one of your web forms. Create a form with first name, last name, company, phone, country and message, then set `PUBLIC_CONTACT_FORM_KEY` to its key. Enquiries arrive as contacts with the form's tag, so a workflow on *Form submitted* can alert your team.
