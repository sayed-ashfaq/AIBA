import { Suspense, lazy } from "react";
import SqlToggle from "./SqlToggle";
import Markdown from "./Markdown";
import styles from "./Message.module.css";

// Split out because the charting library is heavy and most turns never draw one — a greeting, a
// web lookup, or any answer that is a single number. Loaded the first time a chart actually
// appears, which is a network round trip the user is already waiting through.
const ChartCard = lazy(() => import("../Chart/ChartCard"));

export default function Message({ message }) {
  const isUser = message.role === "user";

  return (
    <div className={`${styles.row} ${isUser ? styles.rowUser : ""}`}>
      <div className={`${styles.bubble} ${isUser ? styles.bubbleUser : styles.bubbleAssistant}`}>
        {isUser ? <p className={styles.text}>{message.content}</p> : <Markdown>{message.content}</Markdown>}
        {message.data?.chart_image && (
          <Suspense fallback={null}>
            <ChartCard data={message.data} />
          </Suspense>
        )}
        {message.sql != null && <SqlToggle sql={message.sql} />}
      </div>
    </div>
  );
}
