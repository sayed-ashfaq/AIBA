import ChatListItem from "./ChatListItem";
import { PlusIcon, SidebarIcon } from "../common/icons";
import styles from "./Sidebar.module.css";

export default function Sidebar({
  chats,
  activeChatId,
  isLoading,
  error,
  onSelect,
  onNew,
  onRename,
  onDelete,
  onCollapse,
}) {
  return (
    <aside className={styles.root}>
      <div className={styles.top}>
        <button type="button" className={styles.newChatButton} onClick={onNew}>
          <PlusIcon />
          New chat
        </button>
        <button
          type="button"
          className={styles.collapseButton}
          onClick={onCollapse}
          aria-label="Collapse sidebar"
          title="Collapse sidebar"
        >
          <SidebarIcon />
        </button>
      </div>

      <div className={styles.list}>
        {isLoading ? (
          <p className={styles.hint}>Loading…</p>
        ) : error ? (
          <p className={styles.hint}>{error}</p>
        ) : chats.length === 0 ? (
          <p className={styles.hint}>No conversations yet.</p>
        ) : (
          chats.map((chat) => (
            <ChatListItem
              key={chat.id}
              chat={chat}
              isActive={chat.id === activeChatId}
              onSelect={() => onSelect(chat.id)}
              onRename={(title) => onRename(chat.id, title)}
              onDelete={() => onDelete(chat.id)}
            />
          ))
        )}
      </div>
    </aside>
  );
}
