import { useEffect, useRef, useState } from "react";
import styles from "./UserMenu.module.css";

function initialOf(user) {
  return (user.full_name || user.email || "?").trim().charAt(0).toUpperCase();
}

const SCHEMA_MODES = [
  { value: "plain", label: "Full schema" },
  { value: "graph", label: "Schema graph" },
];

export default function UserMenu({ user, onLogout, onSetSchemaMode }) {
  const [isOpen, setIsOpen] = useState(false);
  const [isLoggingOut, setIsLoggingOut] = useState(false);
  const [pendingMode, setPendingMode] = useState(null);
  const [modeError, setModeError] = useState(null);
  const rootRef = useRef(null);

  useEffect(() => {
    if (!isOpen) return undefined;
    const handleClickOutside = (e) => {
      if (rootRef.current && !rootRef.current.contains(e.target)) setIsOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [isOpen]);

  const handleLogout = async () => {
    setIsLoggingOut(true);
    try {
      await onLogout();
    } finally {
      setIsLoggingOut(false);
    }
  };

  const handleModeChange = async (mode) => {
    if (mode === user.schema_mode || pendingMode) return;
    setModeError(null);
    setPendingMode(mode);
    try {
      await onSetSchemaMode(mode);
    } catch (err) {
      setModeError(err.message || "Couldn't change mode");
    } finally {
      setPendingMode(null);
    }
  };

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={styles.trigger}
        onClick={() => setIsOpen((v) => !v)}
        aria-label="Account menu"
      >
        {user.avatar_url ? (
          <img src={user.avatar_url} alt="" className={styles.avatarImg} />
        ) : (
          <span className={styles.avatar}>{initialOf(user)}</span>
        )}
      </button>

      {isOpen && (
        <div className={styles.panel}>
          <div className={styles.info}>
            <div className={styles.name}>{user.full_name || "Account"}</div>
            <div className={styles.email}>{user.email}</div>
          </div>

          <div className={styles.section}>
            <div className={styles.sectionLabel}>Schema for SQL generation</div>
            <div className={styles.segmented} role="group" aria-label="Schema mode">
              {SCHEMA_MODES.map(({ value, label }) => (
                <button
                  key={value}
                  type="button"
                  className={value === user.schema_mode ? styles.segActive : styles.seg}
                  onClick={() => handleModeChange(value)}
                  disabled={Boolean(pendingMode)}
                >
                  {pendingMode === value ? "…" : label}
                </button>
              ))}
            </div>
            <div className={styles.hint}>
              {user.schema_mode === "graph"
                ? "Experimental — only the question-relevant tables are sent."
                : "The full database schema is sent on every question."}
            </div>
            {modeError && <div className={styles.error}>{modeError}</div>}
          </div>

          <button type="button" className={styles.logoutButton} onClick={handleLogout} disabled={isLoggingOut}>
            {isLoggingOut ? "Logging out…" : "Log out"}
          </button>
        </div>
      )}
    </div>
  );
}
