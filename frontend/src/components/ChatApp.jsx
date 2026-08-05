import { useState } from "react";
import ConnectionBar from "./ConnectionBar/ConnectionBar";
import ChatWindow from "./ChatWindow/ChatWindow";
import ChatInput from "./ChatInput/ChatInput";
import SchemaGraphModal from "./SchemaGraphModal/SchemaGraphModal";
import Sidebar from "./Sidebar/Sidebar";
import UserMenu from "./UserMenu/UserMenu";
import { SidebarIcon } from "./common/icons";
import { useConnections } from "../hooks/useConnections";
import { useChat } from "../hooks/useChat";
import { useChatSessions } from "../hooks/useChatSessions";
import styles from "../App.module.css";

export default function ChatApp({ auth }) {
  const connections = useConnections();
  const sessions = useChatSessions();
  const chat = useChat({ onChatCreated: sessions.addChat, onChatUpdated: sessions.touchChat });
  const [isGraphOpen, setIsGraphOpen] = useState(false);
  const [isSidebarOpen, setIsSidebarOpen] = useState(true);

  const handleDeleteChat = async (chatId) => {
    await sessions.remove(chatId);
    if (chatId === chat.chatId) chat.newChat();
  };

  return (
    <div className={styles.app}>
      {isSidebarOpen && (
        <Sidebar
          chats={sessions.chats}
          activeChatId={chat.chatId}
          isLoading={sessions.isLoading}
          error={sessions.error}
          onSelect={chat.openChat}
          onNew={chat.newChat}
          onRename={sessions.rename}
          onDelete={handleDeleteChat}
          onCollapse={() => setIsSidebarOpen(false)}
        />
      )}

      <div className={styles.mainColumn}>
        <header className={styles.header}>
          <div className={styles.brand}>
            {!isSidebarOpen && (
              <button
                type="button"
                className={styles.sidebarToggle}
                onClick={() => setIsSidebarOpen(true)}
                aria-label="Open sidebar"
                title="Open sidebar"
              >
                <SidebarIcon />
              </button>
            )}
            <h1>NL2SQL</h1>
            <span>Talk with your database</span>
          </div>
          <div className={styles.headerActions}>
            <ConnectionBar
              connections={connections.connections}
              active={connections.active}
              isLoading={connections.isLoading}
              error={connections.error}
              onActivate={connections.activate}
              onCreate={connections.create}
              onDelete={connections.remove}
              onViewGraph={() => setIsGraphOpen(true)}
            />
            <UserMenu user={auth.user} onLogout={auth.logout} />
          </div>
        </header>

        <main className={styles.main}>
          <ChatWindow messages={chat.messages} isSending={chat.isSending} error={chat.error} />
          <ChatInput onSend={chat.sendMessage} disabled={chat.isSending} />
        </main>
      </div>

      {isGraphOpen && (
        <SchemaGraphModal connectionId={connections.active?.id} onClose={() => setIsGraphOpen(false)} />
      )}
    </div>
  );
}
