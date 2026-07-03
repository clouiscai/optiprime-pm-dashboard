const API_BASE = import.meta.env.VITE_API_URL || `${window.location.origin}/api`;
export const REALTIME_ENABLED = import.meta.env.VITE_ENABLE_REALTIME === "true";

export function getApiBase() {
  return API_BASE;
}

export function getWsUrl(token) {
  const url = new URL(API_BASE);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  url.pathname = "/api/ws";
  url.searchParams.set("token", token);
  return url.toString();
}

export async function apiFetch(path, token, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    ...(options.headers || {}),
    Authorization: `Bearer ${token}`,
  };
  const response = await fetch(`${API_BASE}${path}`, { ...options, headers });
  if (response.status === 401) {
    window.dispatchEvent(new Event("optiprime:unauthorized"));
  }
  if (!response.ok) {
    const message = await response.text();
    throw new Error(message || `Request failed with ${response.status}`);
  }
  if (response.status === 204) return null;
  return response.json();
}

export async function downloadCsv(path, token, filename) {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (!response.ok) throw new Error("Export failed");
  const blob = await response.blob();
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  link.click();
  URL.revokeObjectURL(url);
}
