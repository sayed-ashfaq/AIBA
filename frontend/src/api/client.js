const BASE_URL = import.meta.env.VITE_API_URL || "http://localhost:8010";

export class ApiError extends Error {
  constructor(message, status) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

async function request(path, options = {}) {
  let response;
  try {
    response = await fetch(`${BASE_URL}${path}`, {
      headers: { "Content-Type": "application/json" },
      // the session lives in an httponly cookie — without this, cross-origin requests (frontend
      // dev server vs backend) neither send it nor accept the Set-Cookie that logs someone in
      credentials: "include",
      ...options,
    });
  } catch (err) {
    throw new ApiError(`Can't reach the server — is the backend running? (${err.message})`, 0);
  }

  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.detail || `Request failed (${response.status})`, response.status);
  }

  return response.status === 204 ? null : response.json();
}

// chatId omitted (or null) starts a new conversation — the response carries the id to use from
// then on. The server owns the history now; there is nothing to send back.
export function sendChatMessage(message, chatId) {
  return request("/chat", {
    method: "POST",
    body: JSON.stringify({ message, chat_id: chatId ?? null }),
  });
}

// Same turn as sendChatMessage, over POST /chat/stream: the server sends Server-Sent Events as the
// agent works. `onStep` is called with each `step` payload (a tool starting/finishing, or a named
// stage); the promise resolves with the final `done` payload, which has the same shape
// sendChatMessage returns. An `error` frame — the turn failed after streaming began — rejects.
export async function sendChatMessageStream(message, chatId, { onStep } = {}) {
  let response;
  try {
    response = await fetch(`${BASE_URL}/chat/stream`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      credentials: "include",
      body: JSON.stringify({ message, chat_id: chatId ?? null }),
    });
  } catch (err) {
    throw new ApiError(`Can't reach the server — is the backend running? (${err.message})`, 0);
  }

  if (!response.ok || !response.body) {
    const body = await response.json().catch(() => null);
    throw new ApiError(body?.detail || `Request failed (${response.status})`, response.status);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let done = null;

  // one SSE frame: `event:` and `data:` lines, frames separated by a blank line
  const handleFrame = (frame) => {
    let event = "message";
    const data = [];
    for (const line of frame.split("\n")) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    }
    if (!data.length) return;
    const payload = JSON.parse(data.join("\n"));
    if (event === "step") onStep?.(payload);
    else if (event === "done") done = payload;
    else if (event === "error") throw new ApiError(payload.detail || "Something went wrong.", 500);
  };

  for (;;) {
    const { value, done: streamDone } = await reader.read();
    if (streamDone) break;
    buffer += decoder.decode(value, { stream: true });
    let sep;
    while ((sep = buffer.indexOf("\n\n")) !== -1) {
      const frame = buffer.slice(0, sep);
      buffer = buffer.slice(sep + 2);
      if (frame.trim()) handleFrame(frame);
    }
  }
  if (buffer.trim()) handleFrame(buffer);

  if (!done) throw new ApiError("The server closed the connection before finishing.", 0);
  return done;
}

export function listChats() {
  return request("/chats");
}

export function getChat(chatId) {
  return request(`/chats/${chatId}`);
}

export function renameChat(chatId, title) {
  return request(`/chats/${chatId}`, { method: "PATCH", body: JSON.stringify({ title }) });
}

export function deleteChat(chatId) {
  return request(`/chats/${chatId}`, { method: "DELETE" });
}

export function listConnections() {
  return request("/connections");
}

export function createConnection(payload) {
  return request("/connections", { method: "POST", body: JSON.stringify(payload) });
}

export function activateConnection(id) {
  return request(`/connections/${id}/activate`, { method: "POST" });
}

export function deleteConnection(id) {
  return request(`/connections/${id}`, { method: "DELETE" });
}

export function getSchemaGraph() {
  return request("/connections/schema-graph");
}

// The whole graph as formatted text — every table with columns, types and FK lines, then a
// consolidated edge list. This is what the SQL agent reads in graph schema mode. Pass "plain" for
// the flat (non-graph) schema instead.
export function getSchemaText(schemaType = "graph") {
  return request(`/connections/schema?schema_type=${schemaType}`);
}

export function listAnnotations(connectionId) {
  return request(`/connections/${connectionId}/annotations`);
}

export function upsertAnnotation(connectionId, payload) {
  return request(`/connections/${connectionId}/annotations`, {
    method: "PUT",
    body: JSON.stringify(payload),
  });
}

export function deleteAnnotation(connectionId, annotationId) {
  return request(`/connections/${connectionId}/annotations/${annotationId}`, { method: "DELETE" });
}

export function signup(email, password, fullName) {
  return request("/auth/signup", {
    method: "POST",
    body: JSON.stringify({ email, password, full_name: fullName || undefined }),
  });
}

export function login(email, password) {
  return request("/auth/login", { method: "POST", body: JSON.stringify({ email, password }) });
}

export function logout() {
  return request("/auth/logout", { method: "POST" });
}

export function getCurrentUser() {
  return request("/auth/me");
}

// "plain" (full flat schema) or "graph" (experimental schema_linking slice). Returns the updated
// user. Takes effect on the next chat message.
export function setSchemaMode(schemaMode) {
  return request("/auth/me/schema-mode", {
    method: "PATCH",
    body: JSON.stringify({ schema_mode: schemaMode }),
  });
}

// full-page navigation, not fetch() — the browser itself has to follow the Google redirect chain
export function googleLoginUrl() {
  return `${BASE_URL}/auth/google/login`;
}
