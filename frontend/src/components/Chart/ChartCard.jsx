import { useState } from "react";
import { ChevronDownIcon } from "../common/icons";
import ChartControls from "./ChartControls";
import ChartRenderer from "./ChartRenderer";
import DataTable from "./DataTable";
import { MAX_PLOTTED_POINTS, buildChartData, seriesColor } from "./chartData";
import { useChartTheme } from "./useChartTheme";
import styles from "./ChartCard.module.css";

/**
 * A legend is the dependable identity channel, so it is always present for two or more series —
 * the reader never has to match colours from memory. One series needs none: there is a single
 * colour and the title already says what it is.
 */
function Legend({ spec, keys, colorOf }) {
  if (keys.length < 2) return null;

  const isLine = spec.type === "line" || spec.type === "area";

  return (
    <ul className={styles.legend}>
      {keys.map((key) => (
        <li key={key} className={styles.legendItem}>
          {/* the key mirrors the mark: a line for lines, a swatch for fills */}
          <span
            className={isLine ? styles.legendLine : styles.legendSwatch}
            style={{ background: colorOf(key) }}
          />
          {key}
        </li>
      ))}
    </ul>
  );
}

function PieLegend({ rows, spec, palette }) {
  const slices = rows.map((row) => (row[spec.x] == null ? "—" : String(row[spec.x])));
  if (slices.length < 2) return null;

  return (
    <ul className={styles.legend}>
      {slices.map((name, index) => (
        <li key={name} className={styles.legendItem}>
          <span className={styles.legendSwatch} style={{ background: seriesColor(palette, index) }} />
          {name}
        </li>
      ))}
    </ul>
  );
}

/**
 * The chart that comes back with an answer.
 *
 * Shown expanded, because a chart nobody opens may as well not exist — and collapsible, because
 * the prose above it is often the whole answer. The chart/table switch is not decoration either:
 * several series colours sit below 3:1 contrast on the light surface, and the table is how every
 * value stays readable regardless.
 */
export default function ChartCard({ data }) {
  const [spec, setSpec] = useState(data.chart);
  const [isOpen, setIsOpen] = useState(true);
  const [view, setView] = useState("chart");
  const theme = useChartTheme();

  const { keys, colorOf, sampled, total } = buildChartData(spec, data.rows, theme.series);

  // fewer rows than the query returned means this answer was reopened and what came back is the
  // evenly-spaced sample kept with it. The chart looks the same either way, but a table showing 200
  // rows drawn from across the result is a different claim than the first 200, so it gets said.
  const isStoredSample = data.rows.length < data.row_count;

  return (
    <section className={styles.card}>
      <header className={styles.header}>
        <button type="button" className={styles.toggle} onClick={() => setIsOpen((open) => !open)}>
          <ChevronDownIcon open={isOpen} />
          <span className={styles.title}>{spec.title}</span>
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
          {spec.reason && <p className={styles.reason}>{spec.reason}</p>}

          {view === "chart" ? (
            <>
              <ChartRenderer spec={spec} rows={data.rows} theme={theme} />
              {spec.type === "pie" ? (
                <PieLegend rows={data.rows} spec={spec} palette={theme.series} />
              ) : (
                <Legend spec={spec} keys={keys} colorOf={colorOf} />
              )}
            </>
          ) : (
            <DataTable columns={data.columns} rows={data.rows} />
          )}

          <ChartControls spec={spec} profile={data.profile} onChange={setSpec} />

          {(sampled || isStoredSample || data.truncated) && (
            <p className={styles.note}>
              {/* outermost fact first: what was kept, then what is drawn from it */}
              {isStoredSample &&
                `${data.rows.length.toLocaleString()} of the ${data.row_count.toLocaleString()} rows the query returned were kept with this answer, spread evenly across it. `}
              {sampled &&
                (isStoredSample
                  ? `Plotting ${MAX_PLOTTED_POINTS.toLocaleString()} points of those, evenly spaced. `
                  : `Plotting ${MAX_PLOTTED_POINTS.toLocaleString()} points evenly spaced across ${total.toLocaleString()}. `)}
              {data.truncated && `The query returned more rows than the ${data.row_count.toLocaleString()}-row cap.`}
            </p>
          )}
        </>
      )}
    </section>
  );
}
