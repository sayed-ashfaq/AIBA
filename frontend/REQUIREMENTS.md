# NL2SQL Frontend — Requirements

A simple web UI for the NL2SQL assistant: connect to a database, ask questions in plain English, see the answer. This is a requirements doc, not a build — no frontend code exists yet.

## Tech

- React, plain JavaScript (no TypeScript) — keep the setup light, e.g. Vite + React.
- No state-management library needed yet — local component state / Context is enough for a single chat view.

## Screens / flow

1. **Connection screen/toggle** — no longer blocked, `/connections` exists:
   - A form to save a new connection: name, db type (Postgres/MySQL), and either structured fields (host/port/user/password/dbname) or a single raw connection URL — support both input modes.
   - A **toggle/dropdown listing saved connections** (`GET /connections`) showing which one is currently active. Selecting one calls `POST /connections/{id}/activate` to switch.
   - Only one connection is active at a time — this switches which database `sql_agent` queries, it's not per-conversation.
   - Support deleting a saved connection (`DELETE /connections/{id}`).
   - Never display a saved password back in the UI once submitted — the list endpoint doesn't return it either.
   - Next to the active connection, a **"View Graph" button** — see [Schema graph view](#schema-graph-view-view-graph-button) below.
2. **Chat screen** — a message box + send button, scrolling history of user messages and agent replies. Calls `POST /chat`, sending back the `history` array from the previous response each time (see contract below) — the server keeps no session state itself.
3. **Per-message rendering** — each agent reply is one of three shapes, and the UI must handle all three:
   - **Plain answer only** — just render the LLM's natural-language response. This is the default/common case.
   - **Answer + generated SQL** — render the natural-language answer, plus the **SQL toggle** — see [Generated SQL toggle](#generated-sql-toggle) below. Don't show the SQL toggle at all if no SQL was generated for that message.
   - **Answer + chart** — render the natural-language answer, plus the chart described by `data.chart`. Don't reserve chart space if no chart was generated.
4. **Loading state** while waiting on a reply — LLM calls can take a few seconds, don't leave the UI looking frozen.
5. **Error state** if the request fails (network error or non-2xx) — show a readable message, don't swallow it silently.

## API contract (current — will evolve as knowledge_agent becomes real)

```
POST /chat
  request:  { "message": string, "history"?: [{ "role": "user"|"assistant", "content": string }] }
  response: { "reply": string, "routed_to": "sql_agent" | "knowledge_agent" | "visualizer" | "respond",
              "sql": string | null, "history": [...] }

POST   /connections                  { "name", "db_type", "host"?, "port"?, "user"?, "password"?, "dbname"?, "url"? } -> { "id", "name", "db_type", "dbname" }
GET    /connections                  -> [{ "id", "name", "db_type", "dbname", "active" }]
POST   /connections/{id}/activate    -> { "id", "name", "db_type", "dbname" }
DELETE /connections/{id}             -> { "deleted": id }

GET    /connections/schema?schema_type=plain|graph -> { "schema_type": "plain"|"graph", "schema_text": string }
```

`sql` is live now: whenever `routed_to` is `sql_agent` **and** a query actually executed successfully, `sql` is the exact (cleaned/formatted) SQL that ran. It's `null` for every other case — plain conversational replies, knowledge_agent/visualizer responses, and sql_agent attempts that got blocked (write/destructive query refused) or gave up after retries. Key the SQL toggle off the presence of this field, not off `routed_to` alone.

`data` is live now, and carries the rows behind an answer whenever a query ran:

```
data: {
  columns: string[], rows: object[], row_count: number, truncated: boolean,
  chart: { type: "bar"|"line"|"area"|"pie"|"scatter", x: string, y: string[],
           series: string | null, title: string, reason: string } | null,
  profile: [{ name, role: "temporal"|"numeric"|"categorical", distinct, nulls }]
} | null
```

The chart is **described, not drawn**: nothing here is code, and the client renders it. `chart` is null far more often than not — most answers are a sentence, and a chart of one is decoration — so key the chart card off `data.chart`, never off the presence of `data`. `profile` ships whenever rows do, chart or not: it's what lets the client offer a different chart than the one chosen server-side without asking again.

`rows` can be shorter than `row_count`. That means the payload was reopened from storage and trimmed to a size budget by even-stride sampling — say so in the UI rather than presenting a sample as the whole result.

## Explicit non-goals right now

- No auth/login
- No multi-conversation history or persistence beyond what `/chat`'s `history` round-trip gives you (client holds it, not the server)
- No concurrent/per-user active connections — activating a connection changes it for everyone hitting this backend, it's not scoped per browser session

## Generated SQL toggle

Applies to any chat message where the response has a non-null `sql` field (see API contract above).

- Render as a **collapsed-by-default toggle** under the natural-language answer — a small button/link labeled something like "Show SQL" / "Hide SQL", not an always-open panel. The answer is the primary content; the SQL is supporting detail.
- Expanded state shows the SQL in a **monospace, syntax-highlighted block** (any lightweight highlighter is fine — e.g. `prism` or `highlight.js` with a SQL grammar). Preserve the formatting/indentation exactly as returned; don't re-flow or minify it.
- A **copy-to-clipboard** button on the SQL block is a nice-to-have, not required for v1.
- Toggle state is per-message and local to that message — expanding the SQL on one answer shouldn't affect any other message in the thread, and doesn't need to persist across a page reload.
- If `sql` is `null`/absent, render nothing for this — no empty toggle, no placeholder text.

## Schema graph view ("View Graph" button)

A button (next to the active connection, per point 1 above) that opens a modal or side panel showing the **structure** of the connected database: every table as a node, every foreign key as an edge connecting two tables — this is for a user to visually explore "what tables exist and how do they relate", separate from asking chat questions.

**Backend data today:** `GET /connections/schema?schema_type=graph` returns `{ "schema_text": string }` — one flattened text blob (table blocks with columns, then an `Edges (FK relationships):` section listing `schema.table.column -> schema.table.column` lines). It was built for human/LLM reading, not for a UI to parse into a diagram.

Two ways to build this, pick one with backend before starting:

1. **(Recommended) Ask backend for a structured endpoint** — a small addition returning JSON shaped like `{ "nodes": [{ "id": "schema.table", "columns": [{"name", "type", "pk"}] }], "edges": [{ "from": "schema.table", "from_column", "to": "schema.table", "to_column" }] }`, sourced from the same `SchemaGraph` object backend already builds (`app/agents/sql_agent/schema_graph.py`). This is the reliable path for an actual node-link diagram (e.g. via `react-force-graph-2d`, `vis-network`, or `cytoscape.js`) and avoids the UI depending on a text format that's free to change.
2. **(Fallback / prototype only)** Parse `schema_text` client-side: `Table {name}:` starts a node block, indented `- {col} ({type}) PK?` lines are its columns, and the trailing `Edges (FK relationships):` section has one `  - {table}.{col} -> {table}.{col}` line per edge. Workable to get something on screen quickly, but brittle — treat it as throwaway if/when option 1 lands.

Either way:
- For a large schema (AdventureWorks-sized: ~70 tables, ~90 edges) the diagram needs pan/zoom and probably a text filter/search box to find a table by name — don't render it unreadably dense with no way to navigate.
- Clicking/hovering a node should show that table's columns (name, type, PK marker) — the graph is browsable, not just decorative.
- This is a read-only view, no editing.

## Visual design — Claude-inspired palette

Minimal, warm, plenty of whitespace — avoid a "generic SaaS dashboard" look (heavy shadows, saturated blues, dense borders everywhere). One accent color, used sparingly (primary buttons, active states, links) — not decoratively.

**Light mode**
| Token | Hex | Use |
|---|---|---|
| Background | `#FAF9F5` | page background |
| Surface | `#FFFFFF` | cards, message bubbles, panels |
| Border/divider | `#E8E6DC` | hairline borders, dividers |
| Text primary | `#1F1E1D` | body text, headings |
| Text secondary | `#6B6A64` | timestamps, helper text, placeholders |
| Accent | `#D97757` | primary buttons, active tab/toggle, links, focus rings |
| Accent hover | `#C25F3F` | hover/active state of accent elements |

**Dark mode**
| Token | Hex | Use |
|---|---|---|
| Background | `#262624` | page background |
| Surface | `#30302E` | cards, message bubbles, panels |
| Border/divider | `#42413D` | hairline borders, dividers |
| Text primary | `#F5F4ED` | body text, headings |
| Text secondary | `#A8A69E` | timestamps, helper text, placeholders |
| Accent | `#D97757` | same accent both modes — it's warm enough to read on dark |
| Accent hover | `#E8926F` | hover/active state of accent elements |

Notes:
- Treat these as a strong starting point, not pixel-perfect brand values — confirm against Anthropic's official brand assets if strict brand accuracy matters for this project.
- Typography: a clean system sans-serif stack is fine (`-apple-system, "Segoe UI", Inter, sans-serif`) — no need to license a custom font for v1.
- Corners: gently rounded (6–10px), not pill-shaped/bubbly. Shadows: minimal to none — use the border/divider color to separate surfaces instead of drop shadows.
- Respect OS-level dark mode (`prefers-color-scheme`) at minimum; a manual toggle is a nice-to-have, not required for v1.
