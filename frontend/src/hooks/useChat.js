import { useCallback, useRef, useState } from "react";
import { getChat, sendChatMessage } from "../api/client";

let nextId = 0;
const newId = () => `msg-${Date.now()}-${nextId++}`;

// Unary on purpose: `messages.map(toMessage)` would otherwise hand the array index to a second
// parameter, which is a silent wrong answer rather than an error.
//
// `data` comes back on the message itself when a conversation is reopened, and separately on the
// response when a turn has just run — the live copy is the full result, the stored one is trimmed
// to a size bound, so the turn that ran shows everything it fetched.
const toMessage = (m) => ({
  id: m.id,
  role: m.role,
  content: m.content,
  sql: m.sql,
  routedTo: m.routed_to,
  data: m.data ?? null,
});

/**
 * A single conversation.
 *
 * The server owns the history — this holds a chat id, not a transcript, so a refresh or a second
 * tab picks up exactly where the last one left off. `onChatCreated` fires when a message starts a
 * new conversation, which is the sidebar's cue to add a row.
 */
export function useChat({ onChatCreated, onChatUpdated } = {}) {
  const [messages, setMessages] = useState([]);
  const [isSending, setIsSending] = useState(false);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState(null);
  const chatIdRef = useRef(null);

  // start a new conversation: no request needed, the chat row is created by the first message
  const newChat = useCallback(() => {
    chatIdRef.current = null;
    setMessages([]);
    setError(null);
  }, []);

  const openChat = useCallback(async (chatId) => {
    setError(null);
    setIsLoading(true);
    try {
      const chat = await getChat(chatId);
      chatIdRef.current = chat.id;
      setMessages(chat.messages.map(toMessage));
    } catch (err) {
      setError(err.message || "Couldn't open that conversation.");
    } finally {
      setIsLoading(false);
    }
  }, []);

  const sendMessage = useCallback(
    async (text) => {
      const trimmed = text.trim();
      if (!trimmed || isSending) return;

      setError(null);
      // shown immediately; the server's copy replaces nothing, so this keeps its temporary id
      setMessages((prev) => [...prev, { id: newId(), role: "user", content: trimmed }]);
      setIsSending(true);

      try {
        const response = await sendChatMessage(trimmed, chatIdRef.current);
        const isNew = chatIdRef.current === null;
        chatIdRef.current = response.chat_id;

        setMessages((prev) => [
          ...prev,
          { ...toMessage(response.message), data: response.data ?? null },
        ]);

        if (isNew) onChatCreated?.({ id: response.chat_id, title: response.title });
        else onChatUpdated?.(response.chat_id);
      } catch (err) {
        setError(err.message || "Something went wrong. Please try again.");
      } finally {
        setIsSending(false);
      }
    },
    [isSending, onChatCreated, onChatUpdated],
  );

  return {
    chatId: chatIdRef.current,
    messages,
    sendMessage,
    newChat,
    openChat,
    isSending,
    isLoading,
    error,
  };
}
