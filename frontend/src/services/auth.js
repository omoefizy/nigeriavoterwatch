import { api } from "./api";

const KEYS = { access: "nvw_token", refresh: "nvw_refresh" };

function parseJwt(token) {
  try {
    return JSON.parse(atob(token.split(".")[1]));
  } catch {
    return null;
  }
}

export async function login(email, password) {
  const { data } = await api.post("/api/auth/login", { email, password });
  localStorage.setItem(KEYS.access, data.access_token);
  localStorage.setItem(KEYS.refresh, data.refresh_token);
  return data;
}

export function logout() {
  localStorage.removeItem(KEYS.access);
  localStorage.removeItem(KEYS.refresh);
}

export function getAccessToken() {
  return localStorage.getItem(KEYS.access);
}

export function getRole() {
  const token = getAccessToken();
  if (!token) return null;
  return parseJwt(token)?.role ?? null;
}

export function isLoggedIn() {
  const token = getAccessToken();
  if (!token) return false;
  const payload = parseJwt(token);
  if (!payload?.exp) return false;
  return payload.exp * 1000 > Date.now();
}

export async function refreshAccessToken() {
  const refresh = localStorage.getItem(KEYS.refresh);
  if (!refresh) throw new Error("No refresh token");
  const { data } = await api.post("/api/auth/refresh", { refresh_token: refresh });
  localStorage.setItem(KEYS.access, data.access_token);
  return data.access_token;
}
