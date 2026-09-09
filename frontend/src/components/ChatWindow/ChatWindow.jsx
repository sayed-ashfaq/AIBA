import { useEffect, useRef } from "react";
import Message from "../Message/Message";
import ActivityTrail from "../Message/ActivityTrail";
import LoadingDots from "../common/LoadingDots";
import ErrorBanner from "../common/ErrorBanner";
import styles from "./ChatWindow.module.css";

export default function ChatWindow({ messages, isSending, steps, error }) {
  const bottomRef = useRef(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages, isSending, steps]);

  return (
    <div className={styles.window}>
      {messages.length === 0 && !isSending ? (
        <div className={styles.empty}>
          <p>Ask a question about your data in plain English.</p>
        </div>
      ) : (
        messages.map((message) => <Message key={message.id} message={message} />)
      )}

      {isSending && (
        <div className={styles.pendingRow}>
          {steps && steps.length > 0 ? <ActivityTrail steps={steps} live /> : <LoadingDots />}
        </div>
      )}

      <ErrorBanner message={error} />
      <div ref={bottomRef} />
    </div>
  );
}
