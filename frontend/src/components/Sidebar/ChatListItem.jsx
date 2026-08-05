import { useEffect, useRef, useState } from "react";
import { EditIcon, TrashIcon } from "../common/icons";
import styles from "./Sidebar.module.css";

function relativeTime(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return "just now";
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.round(hours / 24);
  if (days < 7) return `${days}d ago`;
  return new Date(iso).toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

export default function ChatListItem({ chat, isActive, onSelect, onRename, onDelete }) {
  const [isEditing, setIsEditing] = useState(false);
  const [draftTitle, setDraftTitle] = useState(chat.title);
  const [confirmingDelete, setConfirmingDelete] = useState(false);
  const [isBusy, setIsBusy] = useState(false);
  const inputRef = useRef(null);

  useEffect(() => {
    if (isEditing) inputRef.current?.select();
  }, [isEditing]);

  const startEditing = (e) => {
    e.stopPropagation();
    setDraftTitle(chat.title);
    setIsEditing(true);
  };

  const commitEdit = async () => {
    setIsEditing(false);
    const title = draftTitle.trim();
    if (!title || title === chat.title) return;
    setIsBusy(true);
    try {
      await onRename(title);
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
      await onDelete();
    } finally {
      setIsBusy(false);
    }
  };

  return (
    <div
      className={`${styles.item} ${isActive ? styles.itemActive : ""}`}
      onClick={isEditing ? undefined : onSelect}
    >
      {isEditing ? (
        <input
          ref={inputRef}
          className={styles.renameInput}
          value={draftTitle}
          onChange={(e) => setDraftTitle(e.target.value)}
          onClick={(e) => e.stopPropagation()}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (e.key === "Enter") e.currentTarget.blur();
            if (e.key === "Escape") setIsEditing(false);
          }}
        />
      ) : (
        <div className={styles.itemInfo}>
          <span className={styles.itemTitle}>{chat.title}</span>
          <span className={styles.itemMeta}>{relativeTime(chat.updated_at)}</span>
        </div>
      )}

      {!isEditing && (
        <div className={styles.itemActions}>
          <button
            type="button"
            className={styles.iconButton}
            onClick={startEditing}
            disabled={isBusy}
            aria-label="Rename chat"
          >
            <EditIcon />
          </button>
          <button
            type="button"
            className={`${styles.iconButton} ${confirmingDelete ? styles.iconButtonDanger : ""}`}
            onClick={handleDeleteClick}
            onBlur={() => setConfirmingDelete(false)}
            disabled={isBusy}
            aria-label={confirmingDelete ? "Confirm delete" : "Delete chat"}
          >
            <TrashIcon />
          </button>
        </div>
      )}
    </div>
  );
}
