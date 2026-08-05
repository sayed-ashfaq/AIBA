import { useState } from "react";
import Modal from "../common/Modal";
import styles from "./ColumnCommentModal.module.css";

export default function ColumnCommentModal({ tableId, columnName, initialComment, onSave, onRemove, onClose }) {
  const [comment, setComment] = useState(initialComment || "");
  const [isSaving, setIsSaving] = useState(false);
  const [error, setError] = useState(null);

  const handleSave = async () => {
    const trimmed = comment.trim();
    if (!trimmed) return;
    setIsSaving(true);
    setError(null);
    try {
      await onSave(trimmed);
      onClose();
    } catch (err) {
      setError(err.message || "Could not save comment.");
      setIsSaving(false);
    }
  };

  const handleRemove = async () => {
    setIsSaving(true);
    setError(null);
    try {
      await onRemove();
      onClose();
    } catch (err) {
      setError(err.message || "Could not remove comment.");
      setIsSaving(false);
    }
  };

  return (
    <Modal title="Column comment" onClose={onClose}>
      <div className={styles.body}>
        <p className={styles.target}>
          {tableId}.{columnName}
        </p>
        <textarea
          autoFocus
          className={styles.textarea}
          placeholder="What is this column for?"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          rows={4}
          disabled={isSaving}
        />
        {error && <p className={styles.error}>{error}</p>}
        <div className={styles.actions}>
          {initialComment ? (
            <button type="button" className={styles.removeButton} onClick={handleRemove} disabled={isSaving}>
              Remove comment
            </button>
          ) : (
            <span />
          )}
          <div className={styles.rightActions}>
            <button type="button" className={styles.cancelButton} onClick={onClose} disabled={isSaving}>
              Cancel
            </button>
            <button
              type="button"
              className={styles.saveButton}
              onClick={handleSave}
              disabled={isSaving || !comment.trim()}
            >
              {isSaving ? "Saving…" : "Save"}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
