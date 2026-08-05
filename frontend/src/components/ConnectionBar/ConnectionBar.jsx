import { useEffect, useRef, useState } from "react";
import ConnectionForm from "./ConnectionForm";
import ConnectionListItem from "./ConnectionListItem";
import { ChevronDownIcon } from "../common/icons";
import styles from "./ConnectionBar.module.css";

export default function ConnectionBar({
  connections,
  active,
  isLoading,
  error,
  onActivate,
  onCreate,
  onDelete,
  onViewGraph,
}) {
  const [isOpen, setIsOpen] = useState(false);
  const [isAdding, setIsAdding] = useState(false);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handleClickOutside = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) {
        setIsOpen(false);
        setIsAdding(false);
      }
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  return (
    <div className={styles.root} ref={rootRef}>
      <button type="button" className={styles.trigger} onClick={() => setIsOpen((v) => !v)}>
        <span className={`${styles.dot} ${active ? styles.dotActive : ""}`} />
        {active ? active.name : "No connection"}
        <ChevronDownIcon open={isOpen} />
      </button>

      <button
        type="button"
        className={styles.graphButton}
        onClick={onViewGraph}
        disabled={!active}
        title={active ? "View schema graph" : "Connect to a database first"}
      >
        View Graph
      </button>

      {isOpen && (
        <div className={styles.panel}>
          {error && <p className={styles.formError}>{error}</p>}

          {isLoading ? (
            <p className={styles.hint}>Loading connections…</p>
          ) : connections.length === 0 ? (
            <p className={styles.hint}>No saved connections yet.</p>
          ) : (
            <ul className={styles.list}>
              {connections.map((c) => (
                <ConnectionListItem key={c.id} connection={c} onActivate={onActivate} onDelete={onDelete} />
              ))}
            </ul>
          )}

          {isAdding ? (
            <ConnectionForm
              onSubmit={async (payload) => {
                await onCreate(payload);
                setIsAdding(false);
              }}
              onCancel={() => setIsAdding(false)}
            />
          ) : (
            <button type="button" className={styles.addButton} onClick={() => setIsAdding(true)}>
              + Add connection
            </button>
          )}
        </div>
      )}
    </div>
  );
}
