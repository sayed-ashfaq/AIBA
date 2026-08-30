# AIBA — Your AI Business Analyst

AIBA lets you ask questions about your business, in plain English, and get straight answers — no spreadsheets, no SQL, no waiting on someone else to pull a report.

> "Which customers brought in the most revenue last year?"
> "Why did deliveries slow down in March?"
> "Show me monthly sales as a chart."

Ask like you would ask a person. AIBA looks at your business data and answers.

## Who this is for

Founders, business owners, and managers who understand their business but don't write code. If you can describe the question, AIBA can go find the answer.

## What you get back

- **A plain-English answer** — not a table of numbers to interpret yourself.
- **A chart**, when a picture makes the trend clearer than words.
- **The receipts** — every answer can show you exactly what data it looked at, so you're never just taking its word for it.

## Is my data safe?

- AIBA only **reads** your data. It cannot change, delete, or add anything to your database — that's enforced in multiple independent ways, not just a polite request to the AI.
- Your database login details are encrypted, and only accessible to you.
- Sign in with Google or an email/password — your account and your connected data are yours alone.

## How it works, in one picture

```
You ask a question
        |
        v
AIBA figures out what you need and looks at your data
        |
        v
You get an answer — with a chart if it helps, and the data to back it up
```

Behind the scenes, AIBA is a small team of specialized AI agents working together and double-checking each other's work — but you never need to think about that part. You just ask.

## Meet the team

| Agent | Think of it as... | What it does |
|---|---|---|
| **Orchestrator** | The manager | Reads your question, decides which specialist(s) below can answer it, and hands you the final answer |
| **SQL Agent** | The data specialist | Writes a query, checks it's safe (read-only, nothing that could change your data), runs it, and hands back the numbers |
| **Python Agent** | The analyst | Takes numbers already fetched and does the math — growth rates, comparisons, combining results — never invents a number that isn't backed by real data |
| **Visualizer** | The chart maker | Turns a result into a chart, in a safe sandbox, when a picture explains it better than a sentence |
| **Verifier** | The reviewer | Checks, after the fact, that the approach the team took actually made sense for your question |
| **Knowledge Agent** | *(coming soon)* | Will answer general company-knowledge questions beyond what's in your database |

Each specialist only has the tools it needs for its one job — the data specialist can query and double-check its own queries, the analyst can only crunch numbers that were already fetched, the chart maker can only draw pictures of real results. Nobody can touch your data outside of that.

## Status

AIBA is early and under active development. It's already useful for straightforward business questions (revenue, top customers, trends over time); more complex questions are still being hardened. Think of it as a capable analyst who's still learning the ropes — worth double-checking on anything high-stakes for now.

## Want to try it or set it up?

Getting AIBA running today still takes a developer's help — see `dev-notes/how_to_run.md`, or just ask the person who shared this with you.
