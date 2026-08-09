import { useState } from "react";
import { ChevronDownIcon } from "../common/icons";
import Markdown from "./Markdown";
import styles from "./ReasoningToggle.module.css";

/**
 * An independent, after-the-fact review of this turn's approach — not another restatement of the
 * answer, and not written by the same model that gave it. Collapsed by default: most answers don't
 * need checking, and this is here for the ones a user wants to verify rather than just trust.
 */
export default function ReasoningToggle({ reasoning }) {
  const [isOpen, setIsOpen] = useState(false);

  return (
    <div className={styles.wrapper}>
      <button type="button" className={styles.toggle} onClick={() => setIsOpen((v) => !v)}>
        <ChevronDownIcon open={isOpen} />
        {isOpen ? "Hide reasoning" : "Reasoning behind the answer"}
      </button>

      {isOpen && (
        <div className={styles.block}>
          <Markdown>{reasoning}</Markdown>
        </div>
      )}
    </div>
  );
}
