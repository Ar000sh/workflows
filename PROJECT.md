# Project Brief: AI Automation, Learn → Build → Sell

> Living document. Update it when a decision is made or a goal changes.
> Grilling sessions should leave their conclusions here, under **Decisions log**.

## Current status (update at the end of every session)
- **A1 lead responder:** layers 1-3 done, tested, reviewed by Tariq, attacked by a fresh agent (fixes committed). Live eval scores are in the A1 README. The drafter eval has not been re-run since the 2026-10-23 guardrail changes (optional, costs about $0.45).
- **Next:** layer 4, split into 4a data, 4b intake API and sender, 4c auth and audit, 4d dashboard, 4e demo site and end-to-end. The 4a plan is `docs/superpowers/plans/2026-10-23-a1-layer-4a-data-foundation.md`, waiting for Tariq's go and his choice of execution method. Layer 4 failure modes: A1 spec section 14.
- **Running things:** `uv run pytest`. DB tests need Docker Desktop running and `docker compose -f track-a-dental/a1-lead-responder/docker-compose.yml up -d postgres mailpit` (they skip, not fail, without it).
- **Setup:** the git hook is enabled per clone (`git config core.hooksPath .githooks`). Skills: `/edge-case-probe`, `/run-evals`, `/retro`. Everything we added is in `devsetup/CATALOG.md`.

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

## Parallel goal: build a reusable dev setup as we go
Alongside the client projects, we grow a **personal AI dev setup** that is ready to reuse on any future client project. It is built *from real need*, not up front: when a build hurts or repeats, we ask "should this be a skill / agent / hook / template?" and add it only if it earns its place.
- **Skills**: repeatable procedures (e.g. new-track scaffold, eval run, ROI-README writer).
- **Agents (subagents)**: only where isolation or a different model pays off (e.g. reviewer, researcher).
- **Hooks**: automatic guardrails (e.g. block committing `.env`, run tests/lint after edits).
- **Plugins / MCP servers**: adopt when they clearly work well; record what and why.
- **Context engineering**: how we structure CLAUDE.md, memory, specs, what goes in context vs. retrieved, and how we keep sessions small and focused.
- **Model & reasoning templates**: for each kind of task, a ready-to-use choice of model (Haiku / Sonnet / Opus), thinking on/off and effort level, with the *why* and measured cost/quality. These feed both our own Claude Code usage and the client builds (e.g. Haiku classifies, Sonnet drafts).
- **Home:** `devsetup/` (templates, notes, `CATALOG.md` logging each addition: what, why, when it helped). Live config stays in `.claude/`.
- **Rule:** every project ends with a short "what should enter the setup?" review.

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
  devsetup/            # reusable skills/agents/hooks/prompt + model templates, CATALOG.md
  .claude/             # live project config (settings, hooks, skills, agents)
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
- **Knowledge feedback loop (A1, after layer 4):** staff suggest FAQ entries or good edited replies from the dashboard, and an approver accepts them into a versioned FAQ / few-shot examples; gap detection lists unanswerable routine questions. Who approves (owner only or any staff)? Layer 4b or a new layer 7? See track-a-dental/a1-lead-responder/REVIEW.md step 8.
- **Dev setup:** which first items go in (candidates: secrets-guard hook, eval-run skill, model-choice template)? Do we also want a per-project "what enters the setup?" checklist step in the definition of done?

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
| 2026-10-22 | **Parallel goal: grow a reusable dev setup** (skills, agents if worth it, hooks, plugins, context-engineering practice, model/effort/reasoning templates) in `devsetup/` + `.claude/`, added only when a real need shows up | Tariq wants templates ready for client work; need-driven avoids over-engineering |
| 2026-10-22 | Dev setup batch 1 (A1 retrospective): secrets guard hook, tests-before-commit git hook (docs-only skips, 90 s limit then ask Tariq), skills `edge-case-probe` and `run-evals` (asks before spending), CLAUDE.md engineering rules, `devsetup/CATALOG.md`, `.gitattributes`. No custom agents (built-in `/code-review` suffices) | Each item fixes something that actually hurt in A1; see devsetup/CATALOG.md |
