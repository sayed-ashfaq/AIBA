import { useState } from "react";
import styles from "./ConnectionBar.module.css";

export default function ConnectionListItem({ connection, onActivate, onDelete }) {
  const [isBusy, setIsBusy] = useState(false);
  const [confirmingDelete, setConfirmingDelete] = useState(false);

  const handleActivate = async () => {
    if (connection.active || isBusy) return;
    setIsBusy(true);
    try {
      await onActivate(connection.id);
    } finally {
      setIsBusy(false);
    }
  };

  const handleDeleteClick = async (e) => {
    e.stopPropagation();
    if (!confirmingDelete) {
      setConfirmingDelete(true);
      return;
    }
    setIsBusy(true);
    try {
      await onDelete(connection.id);
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <li className={`${styles.item} ${connection.active ? styles.itemActive : ""}`} onClick={handleActivate}>
      <div className={styles.itemInfo}>
        <span className={styles.itemName}>{connection.name}</span>
        <span className={styles.itemMeta}>
          {connection.db_type} · {connection.dbname}
        </span>
      </div>
      {connection.active && <span className={styles.activeBadge}>Active</span>}
      <button
        type="button"
        className={`${styles.deleteButton} ${confirmingDelete ? styles.deleteButtonConfirm : ""}`}
        onClick={handleDeleteClick}
        onBlur={() => setConfirmingDelete(false)}
        disabled={isBusy}
      >
        {confirmingDelete ? "Confirm?" : "Delete"}
      </button>
    </li>
  );
}
