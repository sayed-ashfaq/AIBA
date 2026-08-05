import {
  Area,
  AreaChart,
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Scatter,
  ScatterChart,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  buildChartData,
  buildPieData,
  formatCompact,
  formatFull,
  prefersHorizontal,
  seriesColor,
} from "./chartData";
import styles from "./ChartCard.module.css";

const PLOT_HEIGHT = 280;
const ROW_HEIGHT = 30;
// axis labels live below the plot, not inside it — sizing the box to the plot alone is what gives
// a card its own little scrollbar
const AXIS_BAND = 48;
// room for a category name on a lying-down bar chart, and the point past which one gets shortened
const CATEGORY_AXIS_WIDTH = 170;
const CATEGORY_LABEL_MAX = 26;

// The card arrives with a message that has already finished rendering, so there is nothing for a
// draw-on animation to reveal — it just delays the answer and makes the chart unstable to look at
// while it settles.
const STATIC = { isAnimationActive: false };

const shorten = (value) => {
  const text = String(value ?? "");
  return text.length > CATEGORY_LABEL_MAX ? `${text.slice(0, CATEGORY_LABEL_MAX - 1)}…` : text;
};

// Every colour here is a resolved value, never `var(--token)`: these become SVG presentation
// attributes, which don't understand custom properties. See useChartTheme.
function ChartTooltip({ active, payload, label }) {
  if (!active || !payload?.length) return null;

  return (
    <div className={styles.tooltip}>
      <p className={styles.tooltipLabel}>{formatFull(label)}</p>
      {payload.map((entry) => (
        <p key={entry.dataKey ?? entry.name} className={styles.tooltipRow}>
          {/* a short stroke rather than a filled box — at this density a swatch is data-weight
              ink doing a label's job */}
          <span className={styles.tooltipKey} style={{ background: entry.color }} />
          <span className={styles.tooltipValue}>{formatFull(entry.value)}</span>
          <span className={styles.tooltipName}>{entry.name}</span>
        </p>
      ))}
    </div>
  );
}

export default function ChartRenderer({ spec, rows, theme }) {
  const axisTick = { fill: theme.muted, fontSize: 12 };
  const axisLine = { stroke: theme.axis };
  // A marker large enough to hit, ringed in the surface colour so it stays legible where it
  // crosses its own line or another series.
  //
  // Both paint properties have to be stated outright. A dot with no `fill` inherits the line's
  // `fill="none"`, and one with no `fillOpacity` inherits the area's 0.1 wash — either way it
  // comes out invisible while its 2px surface-coloured ring still lands, punching a small hole
  // through the stroke at every data point. Across a chart that reads as a dashed line.
  const markerAt = (color) => ({ r: 4, strokeWidth: 2, stroke: theme.surface, fill: color, fillOpacity: 1 });
  // solid hairline, horizontal only: dashes read as "threshold" when they are just a grid
  const grid = <CartesianGrid stroke={theme.grid} vertical={false} />;

  if (spec.type === "pie") {
    const slices = buildPieData(spec, rows);
    return (
      <ResponsiveContainer width="100%" height={PLOT_HEIGHT}>
        <PieChart>
          <Pie {...STATIC} data={slices} dataKey="value" nameKey="name" innerRadius="45%" outerRadius="78%" paddingAngle={2}>
            {slices.map((slice, index) => (
              // 2px of surface between slices, which is what separates them — never a stroke
              <Cell
                key={slice.name}
                fill={seriesColor(theme.series, index)}
                stroke={theme.surface}
                strokeWidth={2}
              />
            ))}
          </Pie>
          <Tooltip content={<ChartTooltip />} />
        </PieChart>
      </ResponsiveContainer>
    );
  }

  if (spec.type === "scatter") {
    const measure = spec.y[0];
    return (
      <ResponsiveContainer width="100%" height={PLOT_HEIGHT + AXIS_BAND}>
        <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
          {grid}
          <XAxis
            type="number"
            dataKey={spec.x}
            name={spec.x}
            tick={axisTick}
            axisLine={axisLine}
            tickLine={false}
            tickFormatter={formatCompact}
          />
          <YAxis
            type="number"
            dataKey={measure}
            name={measure}
            tick={axisTick}
            axisLine={axisLine}
            tickLine={false}
            tickFormatter={formatCompact}
            width={56}
          />
          <Tooltip content={<ChartTooltip />} cursor={{ stroke: theme.axis }} />
          <Scatter {...STATIC} data={rows} fill={seriesColor(theme.series, 0)} />
        </ScatterChart>
      </ResponsiveContainer>
    );
  }

  const { data, keys, colorOf } = buildChartData(spec, rows, theme.series);

  if (spec.type === "bar") {
    const horizontal = prefersHorizontal(data, spec.x);
    const height = horizontal
      ? Math.max(PLOT_HEIGHT, data.length * ROW_HEIGHT + AXIS_BAND)
      : PLOT_HEIGHT + AXIS_BAND;

    return (
      <ResponsiveContainer width="100%" height={height}>
        <BarChart
          data={data}
          layout={horizontal ? "vertical" : "horizontal"}
          margin={{ top: 8, right: 16, bottom: 8, left: 0 }}
          barGap={2}
          barCategoryGap="20%"
        >
          {grid}
          {horizontal ? (
            <>
              <XAxis type="number" tick={axisTick} axisLine={axisLine} tickLine={false} tickFormatter={formatCompact} />
              <YAxis
                type="category"
                dataKey={spec.x}
                tick={axisTick}
                axisLine={axisLine}
                tickLine={false}
                width={CATEGORY_AXIS_WIDTH}
                tickFormatter={shorten}
              />
            </>
          ) : (
            <>
              <XAxis dataKey={spec.x} tick={axisTick} axisLine={axisLine} tickLine={false} minTickGap={8} />
              <YAxis tick={axisTick} axisLine={axisLine} tickLine={false} tickFormatter={formatCompact} width={56} />
            </>
          )}
          <Tooltip content={<ChartTooltip />} cursor={{ fill: theme.hover }} />
          {keys.map((key) => (
            <Bar
              {...STATIC}
              key={key}
              dataKey={key}
              name={key}
              fill={colorOf(key)}
              maxBarSize={24}
              // rounded at the data end, square where it meets the baseline
              radius={horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0]}
            />
          ))}
        </BarChart>
      </ResponsiveContainer>
    );
  }

  const Chart = spec.type === "area" ? AreaChart : LineChart;

  return (
    <ResponsiveContainer width="100%" height={PLOT_HEIGHT + AXIS_BAND}>
      <Chart data={data} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
        {grid}
        <XAxis dataKey={spec.x} tick={axisTick} axisLine={axisLine} tickLine={false} minTickGap={24} />
        <YAxis tick={axisTick} axisLine={axisLine} tickLine={false} tickFormatter={formatCompact} width={56} />
        <Tooltip content={<ChartTooltip />} cursor={{ stroke: theme.axis }} />
        {keys.map((key) =>
          spec.type === "area" ? (
            <Area
              {...STATIC}
              key={key}
              type="monotone"
              dataKey={key}
              name={key}
              stroke={colorOf(key)}
              strokeWidth={2}
              // a wash, never a saturated block
              fill={colorOf(key)}
              fillOpacity={0.1}
              dot={data.length <= 60 ? markerAt(colorOf(key)) : false}
              activeDot={{ ...markerAt(colorOf(key)), r: 5 }}
            />
          ) : (
            <Line
              {...STATIC}
              key={key}
              type="monotone"
              dataKey={key}
              name={key}
              stroke={colorOf(key)}
              strokeWidth={2}
              strokeLinecap="round"
              strokeLinejoin="round"
              // dots stop helping once they outnumber the pixels between them
              dot={data.length <= 60 ? markerAt(colorOf(key)) : false}
              activeDot={{ ...markerAt(colorOf(key)), r: 5 }}
            />
          ),
        )}
      </Chart>
    </ResponsiveContainer>
  );
}
