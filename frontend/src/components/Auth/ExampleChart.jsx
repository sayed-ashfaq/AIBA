import styles from "./ExampleChart.module.css";

// Decorative, and hard-coded on purpose: the signed-out homepage has no connection, no query and
// no data, so this is a drawing of the product rather than an instance of it. Real charts come
// from ChartCard, off a spec the server sends.
const VALUES = [30, 42, 38, 55, 61, 74];
const MONTHS = ["Feb", "Mar", "Apr", "May", "Jun", "Jul"];
const BAR_WIDTH = 24;
const GAP = 12;
const CHART_HEIGHT = 64;

export default function ExampleChart() {
  const max = Math.max(...VALUES);

  return (
    <div className={styles.card}>
      <svg
        viewBox={`0 0 ${VALUES.length * (BAR_WIDTH + GAP)} ${CHART_HEIGHT + 10}`}
        className={styles.svg}
        role="img"
        aria-label="Example chart: monthly revenue trend"
      >
        {VALUES.map((value, i) => {
          const height = (value / max) * CHART_HEIGHT;
          return (
            <rect
              key={MONTHS[i]}
              x={i * (BAR_WIDTH + GAP) + GAP / 2}
              y={CHART_HEIGHT - height}
              width={BAR_WIDTH}
              height={height}
              rx={4}
              className={styles.bar}
            />
          );
        })}
      </svg>
      <div className={styles.labels}>
        {MONTHS.map((month) => (
          <span key={month}>{month}</span>
        ))}
      </div>
    </div>
  );
}
