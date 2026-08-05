import Message from "../Message/Message";
import ExampleChart from "./ExampleChart";
import styles from "./ChatShowcase.module.css";

// static, illustrative only — the same Message component the real chat uses, fed fixed example
// turns so the preview matches production styling exactly (including the real SQL toggle)
const EXAMPLES = [
  { id: "e1", role: "user", content: "How many active customers do we have right now?" },
  { id: "e2", role: "assistant", content: "You currently have **4,832 active customers** — up 6% from last month." },
  { id: "e3", role: "user", content: "Which product category brought in the most revenue this quarter?" },
  {
    id: "e4",
    role: "assistant",
    content: "**Electronics** led the quarter with **$482,300** in revenue, about 34% of total sales.",
    sql: "SELECT c.name AS category, SUM(oi.quantity * oi.unit_price) AS revenue\nFROM order_items oi\nJOIN products p ON p.id = oi.product_id\nJOIN categories c ON c.id = p.category_id\nWHERE oi.created_at >= date_trunc('quarter', now())\nGROUP BY c.name\nORDER BY revenue DESC\nLIMIT 1;",
  },
  { id: "e5", role: "user", content: "Plot the monthly trend for that category." },
  { id: "e6", role: "assistant", content: "Revenue has grown steadily over the last 6 months.", chart: true },
];

export default function ChatShowcase() {
  return (
    <div className={styles.panel}>
      <div className={styles.header}>
        <span className={styles.badge}>Example conversation</span>
        <h2>See it in action</h2>
        <p>Ask questions in plain English — get answers, the SQL behind them, and visuals, all in one place.</p>
      </div>

      <div className={styles.thread}>
        {EXAMPLES.map((message) => (
          <div key={message.id}>
            <Message message={message} />
            {message.chart && <ExampleChart />}
          </div>
        ))}
      </div>
    </div>
  );
}
