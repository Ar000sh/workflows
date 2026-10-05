# CLAUDE.md

This repo is Tariq's lab and portfolio for learning **AI automation and AI workflows for businesses**, with the goal of freelancing.

**Read the project brief first. It defines the goals, tracks, open questions and decisions:**
@PROJECT.md

## How to work in this repo
- **Teach while building.** Tariq is learning, so explain *why* a design choice was made (briefly) and point out the concept being practiced (e.g. "this is chunking", "this is tool use").
- **Every project must be sellable.** Tie each build to one of the 5 business outcomes in PROJECT.md and note its ROI math in that project's README.
- **Fake but realistic data.** Generate sample data per fictional business inside that track's `data/` folder. Never use real customer data.
- **Reuse `shared/`.** Put LLM, embedding and vector-DB helpers there, not copied into each track.
- **Secrets** go in `.env` (never committed); keep a `.env.example` up to date.
- **n8n flows** are exported as JSON into the track's `n8n/` folder so they are versioned.
- **Definition of done** for a project: runs end-to-end from its README, has a demo script, and RAG bots have an eval set with a recorded score.

## Keeping the brief current
- When a decision is made (in a grilling session or otherwise), add it to **Decisions log** in PROJECT.md and remove it from **Open questions**.
- Do not start building a track while its blocking open questions are unanswered. Grill first.

## Skills in this project
- `/grill-me` (via `grilling`): stress-test the plan. Grill against the **Open questions** in PROJECT.md and write the conclusions back into it.
