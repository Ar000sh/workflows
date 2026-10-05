# Project Brief: AI Automation, Learn → Build → Sell

> Living document. Update it when a decision is made or a goal changes.
> Grilling sessions should leave their conclusions here, under **Decisions log**.

## Why this project exists
Tariq wants to learn AI automation and AI workflows well enough to **earn money as a freelancer**. The work: integrating AI into real businesses in ways that save them measurable money or time. This repo is both the **learning lab** and the **portfolio**. Every project in it should double as a sales demo.

## Who
- **Tariq**: programmer with experience in several languages. New to AI automation as a business.
- Time budget: **15–25 hrs/week**. Goal: **serious freelance income** (portfolio → repeatable offer → paying clients).

## What success looks like (12 weeks)
1. Three working demo projects, one per business type (below), each runnable end-to-end on fake but realistic data, with a 2-minute demo video.
2. A RAG chatbot with a measured eval score (accuracy on a fixed question set).
3. One productized, fixed-price offer (e.g. "AI Receptionist: setup fee + monthly retainer").
4. 1–3 paying clients or paid pilots.

## What businesses pay for (the lens for every project)
Every build must map to one of these outcomes and come with ROI math ("saves X hrs/week × $Y/hr"):
1. **Never miss a lead**: instant reply and qualification.
2. **Answer questions 24/7**: RAG chatbot on the business's own docs.
3. **Kill repetitive admin**: email triage, data extraction, CRM updates.
4. **Produce content/docs faster**: proposals, descriptions, reports.
5. **Know what's happening**: AI summaries of sales, reviews, tickets.

## Proposed stack (tentative, open to challenge)
| Layer | Choice |
|-------|--------|
| Main language | Python |
| LLM | Claude API (Sonnet for quality, Haiku for cheap/high-volume) |
| Workflow glue / client-facing | n8n (self-hosted via Docker) |
| Custom logic / APIs | FastAPI |
| Vector DB | Supabase (Postgres + pgvector) |
| Embeddings | Voyage AI or an open-source model |
| Hosting | Railway/Render (API), small VPS (n8n) |

## The three learning tracks
Each track is built around a **fictional but realistic business** with generated data.

**Track A: Local service, "BrightSmile Dental Clinic"** *(easiest to sell; fundamentals)*
- A1 Lead responder (n8n): form → AI qualifies → reply in under 60s → logged to CRM/Sheet
- A2 FAQ + booking chatbot (Python RAG + pgvector → Cal.com handoff)
- A3 Review engine: review requests + AI-drafted replies for human approval

**Track B: E-commerce, "Northpeak Outdoor Gear"** *(scale, cost control, evals)*
- B1 Support triage: classify emails, auto-answer the easy ones, route the rest
- B2 Product-expert chatbot: hybrid search, metadata filters, reranking, order-lookup tool
- B3 Bulk product description / SEO pipeline (batch API)

**Track C: B2B agency, "Ledger & Lane Marketing"** *(agents; highest ticket)*
- C1 Lead research agent: company → web research → enriched profile + outreach draft
- C2 Internal knowledge assistant: RAG over Drive docs with auto re-indexing
- C3 Proposal and report generator from call notes and data

## Rough schedule
| Weeks | Focus |
|-------|-------|
| 1 | Fundamentals: Claude API, structured output, tool use; n8n running locally |
| 2–3 | Track A |
| 4–6 | Track B (advanced RAG + evals) |
| 7–9 | Track C (agents); start outreach in parallel |
| 10–12 | Productize the best track, deploy, land clients |

## Planned repo layout
```
workflows/
  00-fundamentals/     # API basics, tool use, structured output
  shared/              # reusable LLM client, embeddings, pgvector helpers, chunker
  track-a-dental/      # data/, n8n/ (exported flows), chatbot/
  track-b-ecommerce/
  track-c-agency/
  business/            # offers, pricing, outreach templates, case studies
```

## Open questions (for grilling)
These are **not decided yet**. Settle them before building, and record the answers in the Decisions log.
- **Niche:** which business type is the real go-to-market target, and which is learning only?
- **Order:** after A1, is A → B → C still the right sequence, or should the most sellable track come next?
- **Scope:** are 9 sub-projects too many for 12 weeks? Which could be cut?
- **Selling:** when does outreach start, through which channel (cold email, LinkedIn, local walk-ins, Upwork), and in which geography/market?
- **Pricing:** setup fee + retainer amounts; paid pilot vs. free pilot.
- **Budget:** monthly spend allowed on APIs, hosting, and tools. ⚠️ An Anthropic Console account + API key is needed **before A1 layer 1**.
- **Definition of done** for each project (demo video? eval threshold? deployed URL?).

## Decisions log
| Date | Decision | Why |
|------|----------|-----|
| 2026-10-05 | Learn across 3 business types (local service, e-commerce, B2B) | Breadth first, then pick a niche |
| 2026-10-05 | 15–25 hrs/week, serious freelance goal | Tariq's stated commitment |
| 2026-10-05 | Target market: **Germany** (DSGVO, WhatsApp, Doctolib, EU hosting) | Tariq is based in Germany; DSGVO competence is a selling point |
| 2026-10-05 | First project = **A1 lead responder**, used as the learning vehicle (fundamentals folded in, built in layers) | Learn inside a real use case, not throwaway exercises |
| 2026-10-05 | Stack: **Python (FastAPI) = brain + dashboard, n8n = channel plumbing** | Testable AI logic + the n8n skill agencies hire for; new channels = new n8n flows |
| 2026-10-05 | A1: website form first, other channels later; hybrid autonomy by risk; dashboard first, email approval second | See spec: docs/superpowers/specs/2026-10-05-a1-lead-responder-design.md |
| 2026-10-05 | A1 grilling: Hannover practice; DE+EN only; only redacted message text goes to Claude; Haiku classifies / Sonnet drafts; real staff logins + audit log; spam protection deferred | Details in the A1 spec |
| 2026-10-05 | **Working mode:** Claude writes each layer and explains it; Tariq reviews and runs. No exercises. | Tariq's choice in grilling (Q1, Q18) |
| 2026-10-05 | Tooling: `uv`, git | Modern standard; one-line setup per track |
