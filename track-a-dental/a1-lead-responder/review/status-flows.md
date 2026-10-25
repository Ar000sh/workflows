# A1 status flows (leads and replies)

> Part of the A1 review set; index in [../REVIEW.md](../REVIEW.md). Diagrams are Mermaid: they render on GitHub and in the VS Code preview with a Mermaid extension. The truth is in code: `LEAD_TRANSITIONS` and `REPLY_TRANSITIONS` in `lead_responder/db/models.py`; if they change, change this file in the same commit. Last checked against the code: 2026-10-25 (layer 4b).

Two things have a status: the **lead** (the person who wrote) and each **reply** (an email we drafted or sent). One lead has one to three replies, so read the diagrams as two machines that run side by side.

## 1. Lead status

```mermaid
stateDiagram-v2
    [*] --> new: form post saved
    new --> classified: AI classified it, language supported
    new --> acknowledged: route 1 (AI failed or late) or route 2 (unsupported language), or suppressed
    classified --> auto_replied: routine auto-reply was SENT
    classified --> acknowledged: acknowledgment written, or the auto-reply failed or is in doubt
    acknowledged --> awaiting_approval: a staff draft was stored
    acknowledged --> done: staff handled it (phone), 4d
    awaiting_approval --> replied: staff sent the draft, 4d
    awaiting_approval --> done: staff closed it, 4d
    auto_replied --> done: staff closed it, 4d
    replied --> done: 4d
    done --> [*]
```

Who moves the lead:
| Move | Done by | Code |
|---|---|---|
| `new -> classified` | `IntakeService._write_result` | `intake.py` |
| `new/classified -> acknowledged` | `Repository.acknowledge_lead` (when the first reply is an acknowledgment) or `suppress_lead` | `repository.py` |
| `classified -> auto_replied` | `ReplyService._update_lead`, only after the auto-reply is really `sent` | `replies.py` |
| `classified -> acknowledged` after a failed auto-reply | `ReplyService._update_lead` (also flags the lead) | `replies.py` |
| `acknowledged -> awaiting_approval` | `IntakeService._write_result` after storing the draft | `intake.py` |
| anything to `done`, `awaiting_approval -> replied` | staff actions in 4d (not built) | none yet |

## 2. Reply status

```mermaid
stateDiagram-v2
    [*] --> drafted: reply row written
    drafted --> approved: staff approves a draft, 4c/4d
    drafted --> rejected: staff rejects a draft, 4d
    drafted --> sending: CLAIM (acknowledgment or auto-reply, no approval needed)
    drafted --> failed: refused before any send
    approved --> sending: CLAIM (approved draft)
    approved --> failed: refused before any send
    sending --> sent: SMTP accepted
    sending --> failed: SMTP refused after 3 tries (known not sent), or the sweep gave up the claim
    failed --> sending: CLAIM again (sweep resend or staff Resend)
    sent --> [*]: final
    rejected --> [*]: final
```

`sending` also has a third exit that is not a table move: **a timeout leaves the reply in `sending`** (outcome unknown). Only the sweep (acknowledgments) or staff (everything else) decide what happens next.

## 3. One lead, both machines: what you will see per scenario

| Scenario | Lead: new → ... | Replies (kind: status) | Email goes out |
|---|---|---|---|
| **Routine**, auto-reply allowed | `new -> classified -> auto_replied` | `auto_reply: drafted -> sending -> sent` | the AI-written auto-reply |
| **Routine** but no auto-reply (`AUTO_REPLY_MODE=outside_hours` and the practice is open, route 4; or the guardrails downgraded the auto-reply), or a **sensitive** message (route 3) | `new -> classified -> acknowledged -> awaiting_approval` | `acknowledgment: drafted -> sending -> sent`, `draft: drafted` (waits for staff) | the fixed acknowledgment now; the draft only after staff approve (4d) |
| **Route 1**: AI down, late (45 s) or raised | `new -> acknowledged`, flagged `needs_manual_review` | `acknowledgment: drafted -> sending -> sent`; no draft | fixed DE+EN acknowledgment (fallback) |
| **Route 2**: language not DE/EN | `new -> acknowledged`, flagged | `acknowledgment` as above; no draft | combined DE+EN acknowledgment |
| **Suppressed**: 4th post from one address in an hour | `new -> acknowledged`, flagged, `send_suppressed` | none | nothing, on purpose |
| **Duplicate** (same email + message within 2 minutes) | no new lead | none | nothing |
| **Auto-reply fails** (SMTP refuses) | `classified -> acknowledged`, flagged | `auto_reply: ... -> failed` | nothing; staff see it |
| **Send times out** (any kind) | lead flagged; an auto-reply moves it to `acknowledged` | `... -> sending` and stays | maybe; unknown |

## 4. The whole path of one post

```mermaid
flowchart TD
    A[Form post] --> B[LeadIn validation: 422 if bad, 413 if too big]
    B --> C[create_lead_if_new]
    C -->|same email and message within 2 min| D[Duplicate: stop, answer 202]
    C -->|new lead| E{more than 3 per hour or 10 per day from this mailbox?}
    E -->|yes| F[suppress_lead: acknowledged, flagged, NO email, NO AI]
    E -->|no| G[Answer 202 now; process runs in the background]
    G --> H{global AI cap or no free pipeline slot?}
    H -->|yes| K[Fixed acknowledgment]
    H -->|no| I[Pipeline: redact, classify, decide, draft, guardrails, 45 s deadline]
    I -->|late, raised, or classifier failed| K
    I -->|result| J[Write result: classification, first reply via acknowledge_lead, staff draft]
    J --> L[ReplyService.send: claim, SMTP, record]
    K --> L
    L -->|accepted| M[reply sent]
    L -->|refused 3 times| N[reply failed, lead flagged]
    L -->|timeout| O[reply stays sending, lead flagged]
    O --> P
    N --> P
    P[Sweep job, every few minutes]
```

## 5. What the sweep does (the safety net, `ReplyService.sweep`)

```mermaid
flowchart TD
    S[run_sweep] --> A[Acknowledgment stuck in sending longer than 10 min, or failed by the sweep itself]
    A -->|fewer than 3 attempts| A1[claim again and resend]
    A -->|3 attempts used| A2[leave for staff]
    S --> B[Auto-reply stuck in sending]
    B --> B1[flag the lead only, never resend: a second medical reply is not harmless]
    S --> C[First reply written but still drafted after 5 min]
    C --> C1[send it through send, which claims]
    S --> D[Lead with no first reply after 5 min, not suppressed, not anonymized]
    D --> D1[fixed acknowledgment fallback]
    S --> E[Any item that raises]
    E --> E1[log class name and id, flag lead, count in errors, go on; exit code 1]
```

Staff drafts are never sent by the sweep.

## 6. Not built yet (4c, 4d)

Approve, edit, reject and resend actions, "mark delivered" or "not delivered" for a stuck `sending` reply, `awaiting_approval -> replied`, and every move to `done`. They will use the same two tables: no new status is planned.
