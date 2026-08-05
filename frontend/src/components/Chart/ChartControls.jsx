import styles from "./ChartCard.module.css";

const TYPES = ["bar", "line", "area", "pie", "scatter"];

// what each chart can put on its x axis. Scatter is the odd one: both of its axes are measures.
const X_ROLES = {
  bar: ["categorical", "temporal"],
  line: ["temporal", "categorical"],
  area: ["temporal", "categorical"],
  pie: ["categorical", "temporal"],
  scatter: ["numeric"],
};

/**
 * Re-pick the chart without asking the server again.
 *
 * The rows and the column profile are already here, so changing the shape is a re-render — no
 * round trip, no model call, no wait. That is most of the argument for a chart the agent
 * *describes* rather than draws: a picture would have to be regenerated for every one of these.
 */
export default function ChartControls({ spec, profile, onChange }) {
  const measures = profile.filter((column) => column.role === "numeric");
  const axisOptions = profile.filter((column) => X_ROLES[spec.type].includes(column.role));

  const setType = (type) => {
    const allowed = profile.filter((column) => X_ROLES[type].includes(column.role));
    const x = allowed.some((column) => column.name === spec.x) ? spec.x : allowed[0]?.name;
    // pie and scatter read one measure; carrying three across from a bar chart would silently
    // drop two and leave the chips looking active
    const y = type === "pie" || type === "scatter" ? spec.y.slice(0, 1) : spec.y;
    onChange({ ...spec, type, x: x ?? spec.x, y, series: type === "pie" ? null : spec.series });
  };

  const toggleMeasure = (name) => {
    const next = spec.y.includes(name) ? spec.y.filter((measure) => measure !== name) : [...spec.y, name];
    if (next.length) onChange({ ...spec, y: next });
  };

  const singleMeasure = spec.type === "pie" || spec.type === "scatter";

  return (
    <div className={styles.controls}>
      <label className={styles.control}>
        <span className={styles.controlLabel}>Chart</span>
        <select className={styles.select} value={spec.type} onChange={(event) => setType(event.target.value)}>
          {TYPES.map((type) => (
            <option key={type} value={type}>
              {type}
            </option>
          ))}
        </select>
      </label>

      {axisOptions.length > 1 && (
        <label className={styles.control}>
          <span className={styles.controlLabel}>{spec.type === "pie" ? "Slice by" : "X axis"}</span>
          <select
            className={styles.select}
            value={spec.x}
            onChange={(event) => onChange({ ...spec, x: event.target.value })}
          >
            {axisOptions.map((column) => (
              <option key={column.name} value={column.name}>
                {column.name}
              </option>
            ))}
          </select>
        </label>
      )}

      {measures.length > 1 && (
        <div className={styles.control}>
          <span className={styles.controlLabel}>{singleMeasure ? "Value" : "Measures"}</span>
          <div className={styles.chips}>
            {measures.map((column) => {
              const active = spec.y.includes(column.name);
              return (
                <button
                  key={column.name}
                  type="button"
                  className={`${styles.chip} ${active ? styles.chipActive : ""}`}
                  aria-pressed={active}
                  onClick={() =>
                    singleMeasure ? onChange({ ...spec, y: [column.name] }) : toggleMeasure(column.name)
                  }
                >
                  {column.name}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
