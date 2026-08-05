import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import styles from "./Markdown.module.css";

const components = {
  table: ({ children }) => (
    <div className={styles.tableWrap}>
      <table>{children}</table>
    </div>
  ),
  a: ({ children, ...props }) => (
    <a {...props} target="_blank" rel="noreferrer">
      {children}
    </a>
  ),
  code: ({ className, children, ...props }) => {
    // remark treats fenced blocks vs inline spans the same way — a `language-*` class only
    // shows up on the fenced ones (react-markdown v9+ dropped the `inline` prop for this check)
    const isBlock = /language-/.test(className || "");
    return isBlock ? (
      <code className={className} {...props}>
        {children}
      </code>
    ) : (
      <code className={styles.inlineCode} {...props}>
        {children}
      </code>
    );
  },
};

export default function Markdown({ children }) {
  return (
    <div className={styles.markdown}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {children}
      </ReactMarkdown>
    </div>
  );
}
