import { useState } from "react";
import { ChevronDownIcon } from "../common/icons";
import styles from "./ActivityTrail.module.css";

// One line per agent tool call, in the order they ran. Shown live under the pending reply so the
// user sees the agent routing, reading the schema, and running SQL instead of a bare spinner —
// then kept, collapsed, on the finished message so the run can be inspected after the fact.

const TOOL_LABELS = {
  get_schema: "Reading the database schema",
  sql_generator: "Writing SQL",
  execute_sql: "Running the query",
  write_todos: "Planning the steps",
  read_file: "Reading a working file",
  write_file: "Saving a working file",
  ls: "Checking working files",
};

const STAGE_LABELS = {
  verifying: "Reviewing the answer",
};

function labelFor(step) {
  if (step.stage) return STAGE_LABELS[step.stage] || step.stage;
  if (step.tool === "task") return `Asking the ${step.subagentType || "sub"}-agent`;
  return TOOL_LABELS[step.tool] || step.tool;
}

function StatusGlyph({ status }) {
  if (status === "running") return <span className={styles.spinner} aria-label="running" />;
  if (status === "error") return <span className={styles.error}>✕</span>;
  return <span className={styles.check}>✓</span>;
}

export default function ActivityTrail({ steps, live = false }) {
  const [isOpen, setIsOpen] = useState(live);

  if (!steps || steps.length === 0) return null;

  const active = [...steps].reverse().find((s) => s.status === "running");
  const totalElapsed = steps.reduce((sum, s) => sum + (s.elapsed || 0), 0);

  const headline = live
    ? active
      ? labelFor(active)
      : "Working…"
    : `Worked through ${steps.length} step${steps.length === 1 ? "" : "s"}` +
      (totalElapsed ? ` · ${totalElapsed.toFixed(1)}s` : "");

  return (
    <div className={styles.wrapper}>
      <button type="button" className={styles.toggle} onClick={() => setIsOpen((v) => !v)}>
        {live && active ? (
          <span className={styles.spinner} aria-hidden />
        ) : (
          <ChevronDownIcon open={isOpen} />
        )}
        <span className={styles.headline}>{headline}</span>
      </button>

      {isOpen && (
        <ol className={styles.list}>
          {steps.map((step) => (
            <li key={step.key} className={styles.step}>
              <div className={styles.stepHead}>
                <StatusGlyph status={step.status} />
                <span className={styles.stepLabel}>{labelFor(step)}</span>
                {step.agent && step.agent !== "orchestrator" && (
                  <span className={styles.badge}>{step.agent}</span>
                )}
                {step.elapsed != null && <span className={styles.elapsed}>{step.elapsed.toFixed(1)}s</span>}
              </div>

              {step.description && <div className={styles.detail}>{step.description}</div>}
              {step.sql && <pre className={styles.sql}>{step.sql}</pre>}
              {Array.isArray(step.todos) && step.todos.length > 0 && (
                <ul className={styles.todos}>
                  {step.todos.map((t, i) => (
                    <li key={i}>{typeof t === "string" ? t : t.content || t.task || JSON.stringify(t)}</li>
                  ))}
                </ul>
              )}
            </li>
          ))}
        </ol>
      )}
    </div>
  );
}
