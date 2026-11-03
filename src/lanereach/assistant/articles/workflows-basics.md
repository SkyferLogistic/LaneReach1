---
slug: workflows-basics
title: Building workflows
category: Automation
summary: Triggers, steps and outputs; building on the canvas; validation, test-run and publishing.
---
A **workflow** is a flow chart that runs for each enrolled contact: a **trigger** decides who enters, then **steps** (send an email, wait, branch, tag, create a task…) run top to bottom. Some steps have several **outputs** (for example *If / else* has `true` and `false`); every output must lead somewhere, usually to another step or a *Stop*.

## Building on the canvas

- Click the **+** under a step's output to add the next step: a searchable step picker opens and the new step is connected.
- Drag from an output dot to empty space and let go: the same picker opens there.
- Click the **+** on a connection to insert a step in between; the steps below move down.
- Drag a connection's end to another step to reconnect it; the **×** removes it.
- Click a step in the palette to add it after the selected step.
- **Undo/redo** with Ctrl+Z / Ctrl+Shift+Z, and **Tidy up** arranges everything neatly.
- Loops, connections into the trigger and self-connections are refused with the reason.

Click a step to edit its settings in the inspector on the right. The trigger can be changed from the trigger step.

## Validation

The canvas checks the workflow as you edit: missing settings, unconnected outputs, loops, too many cold emails in a row (at most 3 on any path) and emails without a wait between them. Publishing is refused until every error is fixed.

## Save, test-run, publish

- **Save** creates a new version. Versions never change; you can open an old one again.
- **Test-run** simulates the workflow on a contact (with a simulated reply if you like) and shows every step, guardrail decision and email, without sending anything.
- **Publish** makes the version live for new enrollments. Contacts already enrolled stay on their version.
- **Pause** stops new steps; **Archive** stops the workflow; **Delete** works when no enrollment is running.

## Does it really run?

Yes. Everything on the canvas is executed by the LaneReach engine: triggers, waits in the recipient's business days, branches, AI steps, CRM steps and the guardrails. Emails are delivered for real only when agent mailboxes are connected and sending is switched to SMTP; otherwise they are recorded in the Outbox (dry run).
