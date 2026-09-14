import { useState } from "react";
import { CopyIcon } from "../common/icons";
import styles from "./CopyMessageButton.module.css";

const RESET_DELAY_MS = 1500;

/**
 * Copies the answer exactly as the model wrote it — markdown source, not the rendered HTML — so
 * pasting it into a doc or another chat keeps the headings, tables and bold text intact instead of
 * flattening them to plain text.
 */
export default function CopyMessageButton({ content }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(content);
      setCopied(true);
      setTimeout(() => setCopied(false), RESET_DELAY_MS);
    } catch (err) {
      console.warn("could not copy message:", err.message);
    }
  };

  return (
    <button type="button" className={styles.button} onClick={handleCopy} title="Copy message as markdown">
      <CopyIcon />
      {copied ? "Copied" : "Copy"}
    </button>
  );
}
