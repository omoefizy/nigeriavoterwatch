import axios from "axios";

export const api = axios.create({
  baseURL: import.meta.env.VITE_API_URL || "http://localhost:8000",
  timeout: 15_000,
  headers: { "Content-Type": "application/json" },
});

api.interceptors.request.use((config) => {
  const token = localStorage.getItem("nvw_token");
  if (token) config.headers.Authorization = `Bearer ${token}`;
  return config;
});

api.interceptors.response.use(
  (r) => r,
  async (err) => {
    const status = err.response?.status;
    // On 401, try refreshing once, then redirect to login
    if (status === 401 && !err.config._retried) {
      err.config._retried = true;
      try {
        const { refreshAccessToken } = await import("./auth");
        const newToken = await refreshAccessToken();
        err.config.headers.Authorization = `Bearer ${newToken}`;
        return api(err.config);
      } catch {
        localStorage.removeItem("nvw_token");
        localStorage.removeItem("nvw_refresh");
        window.location.href = "/login";
      }
    }
    return Promise.reject(err);
  }
);

// ── Elections ──────────────────────────────────────────────────────────────────
export const getElections = (params) =>
  api.get("/api/elections", { params }).then((r) => r.data);

// ── Geography ──────────────────────────────────────────────────────────────────
export const getStates = () =>
  api.get("/api/elections/geography/states").then((r) => r.data);

export const getLGAs = (stateId) =>
  api.get(`/api/elections/geography/states/${stateId}/lgas`).then((r) => r.data);

export const getWards = (lgaId) =>
  api.get(`/api/elections/geography/lgas/${lgaId}/wards`).then((r) => r.data);

export const getPollingUnitsByWard = (wardId) =>
  api.get(`/api/elections/geography/wards/${wardId}/polling-units`).then((r) => r.data);

export const searchPollingUnit = (code) =>
  api.get(`/api/elections/geography/polling-units/by-code/${encodeURIComponent(code)}`).then((r) => r.data);

// ── Results ────────────────────────────────────────────────────────────────────
export const getResults = (params) =>
  api.get("/api/results", { params }).then((r) => r.data);

export const getElectionSummary = (electionId) =>
  api.get(`/api/results/election/${electionId}/summary`).then((r) => r.data);

// ── Anomalies ──────────────────────────────────────────────────────────────────
export const getAnomalies = (params) =>
  api.get("/api/anomalies", { params }).then((r) => r.data);

// ── Observer reports ───────────────────────────────────────────────────────────
export const getReports = (params) =>
  api.get("/api/observers/reports", { params }).then((r) => r.data);

export const submitReport = (data) =>
  api.post("/api/observers/reports", data).then((r) => r.data);

// ── Stats (Analytics dashboard) ────────────────────────────────────────────────
export const getUploadByState = () =>
  api.get("/api/stats/upload-by-state").then((r) => r.data);

export const getScrapeHistory = (limit = 96) =>
  api.get("/api/stats/scrape-history", { params: { limit } }).then((r) => r.data);

export const getAnomalySummary = () =>
  api.get("/api/stats/anomaly-summary").then((r) => r.data);
