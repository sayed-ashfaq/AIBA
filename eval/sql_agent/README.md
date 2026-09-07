# SQL agent eval

Measures how often AIBA's SQL agent returns the **right data** for a question, on a fixed
set of questions you control, across the two schema modes (`plain` = full schema, `graph`
= schema-linking slice). No LangSmith, no external benchmark. One golden set per database;
`dvdrental` first.

It also records two operational signals you already care about:

- **`sql_agent_invocations`** — how many times the orchestrator delegated to `sql_agent`
  for a single question. `> 1` is the "re-activating it for no reason" behaviour you saw.
- **latency** — wall-clock per question, plus mean / p50 / p95 across the run.

---

## Layout

```
eval/sql_agent/
  config.py                  machine-specific settings (URLs, DSNs, log path) + env overrides
  datasets/dvdrental.yaml    <- YOU write the questions + verified reference SQL here
  golden/dvdrental.results.json   generated: the reference result sets (commit this)
  build_golden.py            step 1  run + verify every reference query
  run_eval.py                step 2  drive the live agent, one schema mode per run
  score.py                   step 3  re-run agent SQL, compare to gold, assign a verdict
  report.py                  step 4  aggregate one or two runs -> markdown + csv
  lib/                       compare (result-set equivalence), logparse, dbio, dataset
  runs/                      generated: raw captures, scored results, reports (gitignored)
```

Run everything with the backend's virtualenv (it already has `requests`, `psycopg2`,
`sqlglot`, `pyyaml`):

```bash
cd eval/sql_agent
PY=../../backend/.venv/bin/python      # or: cd ../../backend && uv run ...
```

`lib/compare.py` and `lib/logparse.py` have self-tests — run them directly to see exactly
what the scorer treats as equal:

```bash
$PY lib/compare.py
$PY lib/logparse.py
```

---

## The four steps

Prerequisites: `dvdrental` restored in local Postgres, and the AIBA backend running on
`http://127.0.0.1:8010`. **Nothing else should be hitting the backend while a run is in
progress** — `run_eval.py` reads a byte-range of the shared log file per question to count
delegations, and other traffic pollutes that slice.

```bash
# 1. build + verify the golden result sets (re-run after every edit to the yaml)
$PY build_golden.py --dataset dvdrental

# 2. run the agent once per schema mode
$PY run_eval.py --dataset dvdrental --schema-mode plain
$PY run_eval.py --dataset dvdrental --schema-mode graph

# 3. score each run  (cheap + repeatable — no agent calls)
$PY score.py --run runs/dvdrental__plain__<stamp>
$PY score.py --run runs/dvdrental__graph__<stamp>

# 4. compare the two
$PY report.py --runs runs/dvdrental__plain__<stamp> runs/dvdrental__graph__<stamp>
```

`run_eval.py` creates a dedicated eval account (`sql-eval@aiba.dev` by default) and a
connection named `dvdrental (eval)`, so it never touches your own login or active
connection. It sets `schema_mode` over the API (`PATCH /me/schema-mode`) before each run.

Re-scoring is free. If you change a gold query or `lib/compare.py`, re-run **step 3 only**
— you do not need to re-run the agent.

---

## Building the ground truth dataset

This is the work that actually matters, and the only part you cannot automate away. Aim
for **~30–40 questions** on the first pass, roughly `L1 30% / L2 35% / L3 25% / L4 10%`.
Grow it later; every real bug becomes a new item.

### Where to get the questions

1. **Schema-driven.** Open the `dvdrental` ER diagram. It is a DVD rental store: `film`,
   `category`, `film_category`, `actor`, `film_actor`, `inventory`, `rental`, `payment`,
   `customer`, `address`/`city`/`country`, `staff`, `store`. For each table and each pair
   of joined tables, write the one or two questions a **store manager** would actually ask:
   catalog size, films per category, most-rented films, revenue by month, top customers,
   overdue rentals, films never rented, staff who processed the most payments, revenue by
   store, longest films, customers in a given city.

2. **Persona-driven.** List the recurring reports for "store manager", "regional owner",
   "inventory clerk". Real reports are naturally spread across the difficulty tiers.

3. **Community sets.** The `dvdrental` sample DB (from the PostgreSQL Tutorial) is used in
   dozens of public SQL exercise sets. Reuse their practice questions as *candidates* — but
   you still write and verify the SQL yourself.

4. **Your own logs (highest value).** Real questions people already asked AIBA against this
   DB:

   ```bash
   grep -h "user .*: " ../../backend/logs/aiba*.log | sed 's/.*user [0-9a-f-]*: //' | sort -u
   ```

   Anything a real user typed belongs in the set.

5. **LLM-assisted drafting.** Paste the schema DDL, ask for 15 questions per tier. Use them
   as candidates only — the model's *answers* are not trustworthy ground truth.

### Writing an item

```yaml
items:
  - id: 1
    question: "How many films are in the catalog?"
    gold_sql: |
      SELECT count(*) AS film_count FROM film;
    difficulty: L1
    tags: [count, single-table]

  - id: 7
    question: "Which 5 categories earned the most rental revenue, all time?"
    gold_sql: |
      SELECT c.name AS category, sum(p.amount) AS revenue
      FROM payment p
      JOIN rental r        ON r.rental_id = p.rental_id
      JOIN inventory i     ON i.inventory_id = r.inventory_id
      JOIN film_category fc ON fc.film_id = i.film_id
      JOIN category c      ON c.category_id = fc.category_id
      GROUP BY c.name
      ORDER BY revenue DESC, c.name
      LIMIT 5;
    difficulty: L3
    tags: [join, aggregation, top-n]
    notes: >
      revenue = sum(payment.amount) tied to the rental, not rental counts.
      ORDER BY has c.name as a tie-breaker so the 5th row is deterministic.
```

### How to tell a reference query is gold, not quietly wrong

`build_golden.py` catches the mechanical problems. The rest is on you.

1. **Read the rows, not just the count.** Run the query, look at the actual output. A
   plausible-looking number is the easiest thing to get wrong.

2. **Cross-check a second way.** Compute the same answer with a different query shape — a
   subquery instead of a join, or aggregate from the other direction. If two independent
   queries agree, trust the number. This is the single most effective check.

3. **Partition / sum-back check.** A per-category breakdown must sum to the grand total
   (`sum(amount) FROM payment`). A per-store split must sum to the whole. If it doesn't, a
   join is fanning out or dropping rows.

4. **Fan-out on 1-to-many + SUM.** Any time you join a one-to-many relationship and then
   `sum()`, verify against a version that pre-aggregates in a CTE first. This is the #1
   source of a "gold" query that is actually wrong. `notes:` it when relevant.

5. **Determinism.** `LIMIT` with no total-order `ORDER BY` → "top N" is ambiguous; add a
   unique tie-breaker column to the `ORDER BY`. `build_golden.py` flags the missing case.

6. **No `SELECT *`.** Column drift silently breaks comparison. List columns.

7. **Ambiguity.** If the English could mean two things ("last year" = trailing 12 months or
   the previous calendar year?), pin the interpretation in `notes:` and make `gold_sql`
   match it. If it is genuinely ambiguous, reword the question, or keep it as an `L4` and
   expect a lower score there.

8. **Empty results.** A `0`-row gold is a valid answer *sometimes* (e.g. "films never
   rented" might legitimately be empty for this dataset). Confirm it, then add a `notes:`
   line saying so, so future-you doesn't treat it as a bug.

9. **Re-run `build_golden.py` after every edit** and commit `golden/dvdrental.results.json`.
   A diff in that file is how you notice a gold query's numbers moved.

When a later run flags a mismatch that turns out to be **the agent right and your gold
wrong**: tag it `gold_wrong` in `review.csv`, fix the yaml, rebuild. Expect a few of these
in the first couple of iterations — that is the golden set converging, not a problem.

---

## What the metrics mean

| Metric | Definition | Read it as |
|---|---|---|
| `exec_accuracy` | `exec_match / n` | headline: right-data rate |
| `valid_sql_rate` | `1 − (no_sql + agent_sql_error) / n` | does it even produce runnable SQL |
| `by_difficulty` | accuracy split L1–L4 | where to invest — usually L3/L4 are weak |
| `latency_s` mean/p50/**p95** | wall-clock per question | p95 is the one users feel |
| `sql_agent_invocations` mean / `questions_gt_1` / `total_redundant` | delegations to `sql_agent` per question | the re-activation bug; `> 1` with no schema change is waste |

### Verdicts (`score.py`)

`exec_match` · `exec_mismatch` · `agent_sql_error` (SQL doesn't run) · `no_sql` (agent
answered a data question without querying) · `no_gold` (id missing from the golden file) ·
`transport_error` (`/chat` failed/timed out).

### Failure tags (fill `failure_tag` in each run's `review.csv`)

`wrong_table`, `missing_join`, `wrong_join_key`, `missing_filter`, `wrong_filter`,
`wrong_aggregation`, `wrong_grouping`, `hallucinated_column`, `schema_linking_miss` (graph
mode: the right table never made it into the slice), `ambiguity_misread`,
`wrong_order_or_limit`, `date_logic`, `dedupe`, `gold_wrong` (the eval was wrong, not the
agent).

At this stage the tagged failure list is worth more than the headline number — it tells
you what to fix, and `schema_linking_miss` counts are the direct verdict on `graph` mode.

---

## Notes & limitations

- **Single-process backend, no other traffic** during a run, or the per-question log slice
  gets polluted. If your backend runs multiple workers, run the eval against a single-worker
  instance.
- **Comparison is result-set only.** A query that gets the right rows by luck (wrong logic,
  right answer on this data) scores `exec_match`. That's why the failure taxonomy and a
  spot-read of a few agent SQL strings still matter.
- **Column matching** tries positional first, then any column permutation (≤ 7 columns).
  Aliases are ignored — only values are compared.
- **`graph` mode's first question** in a run pays the schema-linking graph build
  (introspection + per-table LLM descriptions + embeddings). `run_eval.py` does a warmup
  call first so that cost stays out of the measured latencies (`--no-warmup` to disable).
- **Hold some questions back.** Once the set is bigger, keep ~30% out of the loop you tune
  the prompt against, so you're not overfitting the prompt to the eval.
