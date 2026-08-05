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

// full-page navigation, not fetch() — the browser itself has to follow the Google redirect chain
export function googleLoginUrl() {
  return `${BASE_URL}/auth/google/login`;
}
