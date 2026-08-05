// Turning a result set into something a chart library can draw.

// Eight fixed slots, assigned in order and never cycled — a ninth series would have to reuse a
// hue, and under colour-vision deficiency the reader could no longer tell the two apart. The
// backend caps series at 8 and measures at 4 for exactly this reason, so this is a backstop.
export const SERIES_LIMIT = 8;

// Points plotted before we start thinning. Well under what the wire can carry (5000 rows is about
// half a megabyte) — this is the browser's limit, not the network's: every point is an SVG node,
// and a few thousand of them make hover and resize crawl.
export const MAX_PLOTTED_POINTS = 1000;

// index never wraps in practice — the backend caps series at 8 — but a modulo beats an undefined
export const seriesColor = (palette, index) => palette[Math.max(index, 0) % SERIES_LIMIT];

const compactNumber = new Intl.NumberFormat(undefined, { notation: "compact", maximumFractionDigits: 1 });
const fullNumber = new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 });

export const formatCompact = (value) => (typeof value === "number" ? compactNumber.format(value) : value);
export const formatFull = (value) => (typeof value === "number" ? fullNumber.format(value) : String(value ?? "—"));

/**
 * Thin a long series by taking evenly spaced points, always keeping the first and last.
 *
 * Not the same as cropping to the first N. A line chart cropped at 1000 of 5000 points silently
 * redraws the x-axis to a fifth of the range and looks like a complete picture of it — the reader
 * has no way to tell. Striding keeps the full range and the shape of the curve, and only loses
 * resolution between points.
 */
export function downsample(rows, limit = MAX_PLOTTED_POINTS) {
  if (rows.length <= limit) return { rows, sampled: false };

  const stride = (rows.length - 1) / (limit - 1);
  const thinned = Array.from({ length: limit }, (_, i) => rows[Math.round(i * stride)]);
  return { rows: thinned, sampled: true };
}

/**
 * Long to wide: rows of (month, region, sales) become one row per month with a column per region,
 * which is the shape a chart library expects for multiple series.
 *
 * Series keys come back in first-appearance order and that order is what picks each series' colour
 * — so hiding one series never repaints the others. A reader who learned "North is blue" keeps it.
 */
export function pivotSeries(rows, xKey, seriesKey, valueKey) {
  const byX = new Map();
  const keys = [];

  for (const row of rows) {
    const x = row[xKey];
    const name = row[seriesKey] == null ? "—" : String(row[seriesKey]);

    if (!byX.has(x)) byX.set(x, { [xKey]: x });
    byX.get(x)[name] = row[valueKey];
    if (!keys.includes(name)) keys.push(name);
  }

  return { data: [...byX.values()], keys: keys.slice(0, SERIES_LIMIT) };
}

/**
 * What actually gets drawn, for one spec over one result set.
 *
 * `keys` are the series to plot and `colorOf` maps each to its slot. The two are kept separate
 * because a hidden measure must not shift the colours of the ones still on screen.
 */
export function buildChartData(spec, rows, palette) {
  if (spec.series) {
    const { data, keys } = pivotSeries(rows, spec.x, spec.series, spec.y[0]);
    const { rows: plotted, sampled } = downsample(data);
    return {
      data: plotted,
      keys,
      colorOf: (key) => seriesColor(palette, keys.indexOf(key)),
      sampled,
      total: data.length,
    };
  }

  const { rows: plotted, sampled } = downsample(rows);
  const keys = spec.y.slice(0, SERIES_LIMIT);
  return {
    data: plotted,
    keys,
    // indexed against the spec's full measure list, not the visible subset
    colorOf: (key) => seriesColor(palette, spec.y.indexOf(key)),
    sampled,
    total: rows.length,
  };
}

/** Pie wants one slice per category, and slices only mean anything as parts of one total. */
export function buildPieData(spec, rows) {
  const measure = spec.y[0];
  return rows
    .map((row) => ({ name: row[spec.x] == null ? "—" : String(row[spec.x]), value: row[measure] }))
    .filter((slice) => typeof slice.value === "number" && slice.value > 0);
}

/**
 * Bars turn on their side when the categories are many or their names are long.
 *
 * Upright columns give each label the bar's own width, so anything longer collides with its
 * neighbours or gets clipped — and a clipped label is worse than no label. Rotating the chart
 * gives labels a full line of width each.
 */
export function prefersHorizontal(data, xKey) {
  if (data.length > 12) return true;
  const longest = data.reduce((max, row) => Math.max(max, String(row[xKey] ?? "").length), 0);
  return longest > 14;
}
