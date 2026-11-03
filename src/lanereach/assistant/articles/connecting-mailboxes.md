---
slug: connecting-mailboxes
title: Connecting agent mailboxes
category: Outreach
summary: Send for real through each agent's own mailbox (SMTP) and read replies (IMAP).
---
By default LaneReach runs in **dry-run mode**: emails are recorded in the Outbox and nothing is delivered. To send for real, each agent's mailbox is connected and your administrator switches sending to SMTP (`SENDING_PROVIDER=smtp`).

## Connect a mailbox

1. Open **Agents**, click the agent, and find **Mailbox**.
2. Click **Connect mailbox** and choose the provider: Microsoft 365, Google Workspace, Zoho, or *Other* to enter the servers yourself.
3. Enter the username and the password. Use an **app password** when the mailbox has multi-factor sign-in.
4. Click **Save and test**. LaneReach logs in to SMTP and IMAP; nothing is sent or read. The status becomes *Connected* or shows the server's answer.

Provider notes: Microsoft 365 needs SMTP AUTH enabled for the mailbox. Google needs 2-step verification, an app password and IMAP switched on.

## How sending works

- Every email first passes the guardrails, then waits in the Outbox as **queued** and is delivered within seconds through the agent's mailbox.
- If the server is unreachable, LaneReach retries after 1, 5, 15, 60 and 180 minutes, then marks the email **failed** with the reason. A refused login fails at once and marks the mailbox as having a problem.
- Agents without a connected, tested mailbox do not send in SMTP mode: the *mailbox connected* guardrail holds their emails and says why.

## How replies are read

Every minute LaneReach reads **new** messages in the mailbox's inbox over IMAP, without marking them read. Only answers to LaneReach's own emails (and bounces of them) are picked up; other mail is ignored and not stored. Old mail from before the connection is never read.

The password is encrypted and never shown again. Admins connect and disconnect mailboxes.
