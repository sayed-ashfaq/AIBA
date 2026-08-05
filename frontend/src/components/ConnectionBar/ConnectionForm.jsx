import { useState } from "react";
import styles from "./ConnectionBar.module.css";

const emptyStructured = { host: "", port: "", user: "", password: "", dbname: "" };

export default function ConnectionForm({ onSubmit, onCancel }) {
  const [name, setName] = useState("");
  const [dbType, setDbType] = useState("postgres");
  const [mode, setMode] = useState("structured");
  const [structured, setStructured] = useState(emptyStructured);
  const [url, setUrl] = useState("");
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState(null);

  const updateField = (field) => (e) => setStructured((prev) => ({ ...prev, [field]: e.target.value }));

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setIsSubmitting(true);
    try {
      const payload =
        mode === "url"
          ? { name, db_type: dbType, url }
          : { name, db_type: dbType, ...structured, port: structured.port ? Number(structured.port) : undefined };
      await onSubmit(payload);
    } catch (err) {
      setError(err.message || "Could not save connection.");
      setIsSubmitting(false);
    }
  };

  return (
    <form className={styles.form} onSubmit={handleSubmit}>
      <input
        required
        placeholder="Connection name"
        value={name}
        onChange={(e) => setName(e.target.value)}
        className={styles.input}
      />

      <select value={dbType} onChange={(e) => setDbType(e.target.value)} className={styles.input}>
        <option value="postgres">Postgres</option>
        <option value="mysql">MySQL</option>
      </select>

      <div className={styles.modeToggle}>
        <button
          type="button"
          className={mode === "structured" ? styles.modeActive : ""}
          onClick={() => setMode("structured")}
        >
          Fields
        </button>
        <button type="button" className={mode === "url" ? styles.modeActive : ""} onClick={() => setMode("url")}>
          Connection URL
        </button>
      </div>

      {mode === "structured" ? (
        <div className={styles.fieldGrid}>
          <input required placeholder="Host" value={structured.host} onChange={updateField("host")} className={styles.input} />
          <input
            required
            placeholder="Port"
            value={structured.port}
            onChange={updateField("port")}
            className={styles.input}
            inputMode="numeric"
          />
          <input required placeholder="User" value={structured.user} onChange={updateField("user")} className={styles.input} />
          <input
            required
            type="password"
            placeholder="Password"
            value={structured.password}
            onChange={updateField("password")}
            className={styles.input}
          />
          <input
            required
            placeholder="Database name"
            value={structured.dbname}
            onChange={updateField("dbname")}
            className={`${styles.input} ${styles.inputFull}`}
          />
        </div>
      ) : (
        <input
          required
          placeholder="postgresql://user:pass@host:port/dbname"
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          className={styles.input}
        />
      )}

      {error && <p className={styles.formError}>{error}</p>}

      <div className={styles.formActions}>
        <button type="button" onClick={onCancel} className={styles.cancelButton} disabled={isSubmitting}>
          Cancel
        </button>
        <button type="submit" className={styles.submitButton} disabled={isSubmitting}>
          {isSubmitting ? "Saving…" : "Save connection"}
        </button>
      </div>
    </form>
  );
}
