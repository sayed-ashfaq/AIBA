import { formatFull } from "./chartData";
import styles from "./DataTable.module.css";

// The inline chat view never shows more than this — past it, ResultDataViewer's "View full data"
// button is the only way to see the rest, so this has to match its own threshold exactly or one of
// the two starts lying (a capped inline table with no button, or a button with nothing left to add).
export const INLINE_ROW_LIMIT = 10;

/**
 * The chart's readable twin.
 *
 * Not an extra: three of the light-mode series colours sit below 3:1 against the card surface, and
 * the palette's relief rule is that a chart carrying those colours ships a way to read every value
 * without relying on hue at all. It also covers the cases a chart can't — a result with no
 * measure, or one the visualizer declined to plot.
 */
export default function DataTable({ columns, rows, limit = INLINE_ROW_LIMIT, maxHeight }) {
  const visible = rows.slice(0, limit);

  return (
    <div className={styles.wrapper}>
      <div className={styles.scroll} style={maxHeight ? { maxHeight } : undefined}>
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

      {rows.length > limit && (
        <p className={styles.note}>
          Showing {limit.toLocaleString()} of {rows.length.toLocaleString()} rows.
        </p>
      )}
    </div>
  );
}
