import { useCallback, useEffect, useState } from "react";
import * as api from "../api/client";

/**
 * The sidebar's list of conversations.
 *
 * `addChat`/`touchChat` update local state directly instead of refetching — the chat endpoint
 * already told the caller what changed, so a round trip would just be latency for no new info.
 */
export function useChatSessions() {
  const [chats, setChats] = useState([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState(null);

  const refresh = useCallback(async () => {
    setIsLoading(true);
    setError(null);
    try {
      setChats(await api.listChats());
    } catch (err) {
      setError(err.message);
    } finally {
      setIsLoading(false);
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  const addChat = useCallback(({ id, title }) => {
    const now = new Date().toISOString();
    setChats((prev) => [
      { id, title, connection_id: null, message_count: 2, created_at: now, updated_at: now },
      ...prev,
    ]);
  }, []);

  const touchChat = useCallback((chatId) => {
    setChats((prev) => {
      const chat = prev.find((c) => c.id === chatId);
      if (!chat) return prev;
      const touched = { ...chat, message_count: chat.message_count + 2, updated_at: new Date().toISOString() };
      return [touched, ...prev.filter((c) => c.id !== chatId)];
    });
  }, []);

  const rename = useCallback(async (chatId, title) => {
    const updated = await api.renameChat(chatId, title);
    setChats((prev) => prev.map((c) => (c.id === chatId ? updated : c)));
  }, []);

  const remove = useCallback(async (chatId) => {
    await api.deleteChat(chatId);
    setChats((prev) => prev.filter((c) => c.id !== chatId));
  }, []);

  return { chats, isLoading, error, refresh, addChat, touchChat, rename, remove };
}
