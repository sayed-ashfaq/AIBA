import { useState } from "react";
import { ChevronDownIcon } from "../common/icons";
import DataTable from "./DataTable";
import styles from "./ChartCard.module.css";

/**
 * The chart that comes back with an answer.
 *
 * Shown expanded, because a chart nobody opens may as well not exist — and collapsible, because
 * the prose above it is often the whole answer. The chart/table switch is not decoration either:
 * the chart is a fixed image the visualizer subagent rendered (heatmaps, scatter, whatever the
 * data called for — not just what a declarative spec could express), so unlike the old spec-driven
 * chart there is nothing here to re-plot from different columns. The table is what stays fully
 * readable and inspectable regardless of what the image shows.
 */
export default function ChartCard({ data }) {
  const [isOpen, setIsOpen] = useState(true);
  const [view, setView] = useState("chart");

  // fewer rows than the query returned means this answer was reopened and what came back is the
  // evenly-spaced sample kept with it — said explicitly, since a 200-row table drawn from across
  // the result is a different claim than the first 200
  const isStoredSample = data.rows.length < data.row_count;

  return (
    <section className={styles.card}>
      <header className={styles.header}>
        <button type="button" className={styles.toggle} onClick={() => setIsOpen((open) => !open)}>
          <ChevronDownIcon open={isOpen} />
          <span className={styles.title}>{data.chart_title}</span>
        </button>

        {isOpen && (
          <div className={styles.views} role="group" aria-label="Chart or table">
            {["chart", "table"].map((option) => (
              <button
                key={option}
                type="button"
                className={`${styles.viewButton} ${view === option ? styles.viewActive : ""}`}
                aria-pressed={view === option}
                onClick={() => setView(option)}
              >
                {option === "chart" ? "Chart" : "Table"}
              </button>
            ))}
          </div>
        )}
      </header>

      {isOpen && (
        <>
          {data.chart_caption && <p className={styles.caption}>{data.chart_caption}</p>}

          {view === "chart" ? (
            <div className={styles.imageFrame}>
              <img
                className={styles.image}
                src={`data:image/png;base64,${data.chart_image}`}
                alt={data.chart_caption || data.chart_title}
              />
            </div>
          ) : (
            <DataTable columns={data.columns} rows={data.rows} />
          )}

          {(isStoredSample || data.truncated) && (
            <p className={styles.note}>
              {isStoredSample &&
                `${data.rows.length.toLocaleString()} of the ${data.row_count.toLocaleString()} rows the query returned were kept with this answer, spread evenly across it. `}
              {data.truncated && `The query returned more rows than the ${data.row_count.toLocaleString()}-row cap.`}
            </p>
          )}
        </>
      )}
    </section>
  );
}
