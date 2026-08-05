import { useState } from "react";
import Prism from "prismjs";
import "prismjs/components/prism-sql";
import { ChevronDownIcon } from "../common/icons";
import styles from "./SqlToggle.module.css";

export default function SqlToggle({ sql }) {
  const [isOpen, setIsOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(sql);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // clipboard permission denied or unavailable — copy is a nice-to-have, fail quietly
    }
  };

  const highlighted = Prism.highlight(sql, Prism.languages.sql, "sql");

  return (
    <div className={styles.wrapper}>
      <button type="button" className={styles.toggle} onClick={() => setIsOpen((v) => !v)}>
        <ChevronDownIcon open={isOpen} />
        {isOpen ? "Hide SQL" : "Show SQL"}
      </button>

      {isOpen && (
        <div className={styles.block}>
          <button type="button" className={styles.copyButton} onClick={handleCopy}>
            {copied ? "Copied" : "Copy"}
          </button>
          {/* Prism.highlight HTML-escapes the source text as part of tokenizing, so this is safe. */}
          <pre className={styles.pre}>
            <code className="language-sql" dangerouslySetInnerHTML={{ __html: highlighted }} />
          </pre>
        </div>
      )}
    </div>
  );
}
