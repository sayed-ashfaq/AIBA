/**
 * The component vocabulary the visualizer subagent may use, and the prompt options that teach it.
 *
 * One file feeds both ends. The browser renders with `library`; `npm run gen:openui-prompt` feeds
 * the same file to the OpenUI CLI and writes the model's reference text into the backend, so what
 * the model is told it can write and what the renderer can draw can't drift apart.
 *
 * Deliberately a small slice of OpenUI's stock library: layout, a table and the charts. Forms,
 * image cards and the rest would triple the prompt and give the model more ways to go wrong.
 */
import { createLibrary } from "@openuidev/react-lang";
import { openuiLibrary } from "@openuidev/react-ui/genui-lib";

export const ROWS_TOOL = "get_rows";

const stock = openuiLibrary.components;

function pick(...names) {
  return names.map((name) => {
    if (!stock[name]) throw new Error(`OpenUI's stock library has no component named ${name}`);
    return stock[name];
  });
}

export const library = createLibrary({
  root: "Card",
  components: pick(
    "Card",
    "CardHeader",
    "TextContent",
    "Table",
    "Col",
    "BarChart",
    "HorizontalBarChart",
    "LineChart",
    "AreaChart",
    "PieChart",
    "ScatterChart",
    "Series",
    "ScatterSeries",
    "Point",
    "Slice",
  ),
});

export const promptOptions = {
  preamble:
    "openui-lang is a declarative UI language. You write it as the `program` argument of a tool call; it is rendered in the user's browser as an interactive chart or table.",
  tools: [
    {
      name: ROWS_TOOL,
      description:
        "Returns every row of the dataset being visualized. Read a column as an array with data.rows.<column>.",
      inputSchema: { type: "object", properties: {} },
      outputSchema: { type: "object", properties: { rows: { type: "array", items: { type: "object" } } } },
      annotations: { readOnlyHint: true },
    },
  ],
  toolExamples: [
    `root = Card([chart])
chart = LineChart(data.rows.month, [Series("Revenue", data.rows.revenue)], "linear", "Month", "Revenue")
data = Query("${ROWS_TOOL}", {}, {rows: []})`,
  ],
  // The stock "Data Workflow" text assumes the model can call the tool and inline what it finds.
  // Here it can't, and mustn't: the rows are bound in the browser, never typed by the model.
  additionalRules: [
    `You cannot call ${ROWS_TOOL} yourself — only the finished program can, when it runs in the browser. Ignore any instruction above to inspect tool data first.`,
    `The Query default for ${ROWS_TOOL} is ALWAYS {rows: []}. Never copy real rows or values into the program, and never type a number you were told — every plotted value comes from data.rows.<column>.`,
    "Use only the column names you were given for the dataset, spelled exactly.",
    "Wrap everything in root = Card([...]). Do not add a CardHeader, title or caption text: the app already shows the title and caption you pass to render_chart above the chart, and repeating them looks doubled.",
  ],
  bindings: false,
};
