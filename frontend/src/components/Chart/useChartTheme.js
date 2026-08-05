import { useEffect, useState } from "react";
import { SERIES_LIMIT } from "./chartData";

/**
 * The theme's chart tokens, resolved to real colour values.
 *
 * Charts can't simply reference `var(--chart-series-1)` the way the rest of the app does. A chart
 * library writes colours into SVG *presentation attributes* (`fill="…"`, `stroke="…"`), and those
 * are parsed as SVG attribute syntax, which has no notion of custom properties — `fill="var(…)"`
 * is not a colour that resolves later, it's an invalid value, and the mark renders with its
 * initial paint or none at all. Custom properties only work in real CSS declarations, which is why
 * the legend swatches (plain `background`) were fine while the plot came out blank.
 *
 * So the values are read off the document once and re-read when the colour scheme changes, which
 * keeps the palette defined in exactly one place — theme.css — rather than duplicated in JS.
 */
const readVar = (name) => getComputedStyle(document.documentElement).getPropertyValue(name).trim();

function readChartTheme() {
  return {
    series: Array.from({ length: SERIES_LIMIT }, (_, i) => readVar(`--chart-series-${i + 1}`)),
    grid: readVar("--chart-grid"),
    axis: readVar("--chart-axis"),
    muted: readVar("--color-text-secondary"),
    surface: readVar("--color-surface"),
    hover: readVar("--color-accent-bg"),
  };
}

export function useChartTheme() {
  const [theme, setTheme] = useState(readChartTheme);

  useEffect(() => {
    const query = window.matchMedia("(prefers-color-scheme: dark)");
    const refresh = () => setTheme(readChartTheme());
    query.addEventListener("change", refresh);
    return () => query.removeEventListener("change", refresh);
  }, []);

  return theme;
}
