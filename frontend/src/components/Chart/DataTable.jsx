import { formatFull } from "./chartData";
import styles from "./DataTable.module.css";

// enough to scan, not so many that the DOM node count starts to matter — the full set is always
// one "Show SQL" away
const VISIBLE_ROWS = 200;

/**
 * The chart's readable twin.
 *
 * Not an extra: three of the light-mode series colours sit below 3:1 against the card surface, and
 * the palette's relief rule is that a chart carrying those colours ships a way to read every value
 * without relying on hue at all. It also covers the cases a chart can't — a result with no
 * measure, or one the visualizer declined to plot.
 */
export default function DataTable({ columns, rows }) {
  const visible = rows.slice(0, VISIBLE_ROWS);

  return (
    <div className={styles.wrapper}>
      <div className={styles.scroll}>
        <table className={styles.table}>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column} scope="col">
                  {column}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row, index) => (
              <tr key={index}>
                {columns.map((column) => (
                  <td key={column} className={typeof row[column] === "number" ? styles.numeric : undefined}>
                    {formatFull(row[column])}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      {rows.length > VISIBLE_ROWS && (
        <p className={styles.note}>
          Showing {VISIBLE_ROWS.toLocaleString()} of {rows.length.toLocaleString()} rows.
        </p>
      )}
    </div>
  );
}
