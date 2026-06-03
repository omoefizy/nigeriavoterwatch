import React, { useEffect } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../services/api";
import { useWsStore } from "../store/wsStore";

export default function Home() {
  const queryClient = useQueryClient();
  const { connected, lastUpdate, lastAlert } = useWsStore();

  // ── Dashboard stats ───────────────────────────────────────────────────────
  const { data: stats, isLoading } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get("/api/stats/dashboard").then((r) => r.data),
    refetchInterval: 60_000,
  });

  const { data: health } = useQuery({
    queryKey: ["health"],
    queryFn: () => api.get("/api/health").then((r) => r.data),
    refetchInterval: 30_000,
  });

  // ── Refresh stats after each scrape push ─────────────────────────────────
  useEffect(() => {
    if (lastUpdate) {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  }, [lastUpdate, queryClient]);

  const apiOnline = health?.status === "ok";
  const scrape = stats?.latest_scrape;

  return (
    <div>
      <div className="page-header" style={{ display: "flex", alignItems: "baseline", gap: "1rem" }}>
        <h1>Election Integrity Dashboard</h1>
        <span
          style={{
            fontSize: "0.72rem",
            fontWeight: 600,
            padding: "2px 8px",
            borderRadius: 99,
            background: connected ? "#d1fae5" : "#fef3c7",
            color: connected ? "#065f46" : "#92400e",
          }}
        >
          {connected ? "Live" : "Connecting…"}
        </span>
      </div>

      {lastUpdate && (
        <div
          className="offline-banner"
          style={{ background: "#ecfdf5", borderColor: "#34d399", color: "#065f46" }}
        >
          Scrape completed — {lastUpdate.images_downloaded ?? 0} images downloaded,{" "}
          {lastUpdate.new_anomalies ?? 0} new anomalies detected.
          {lastUpdate.completed_at &&
            ` (${new Date(lastUpdate.completed_at).toLocaleTimeString("en-NG", {
              timeZone: "Africa/Lagos",
            })})`}
        </div>
      )}

      {lastAlert && (
        <div className="error-msg" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
          <span>
            <strong>Critical anomaly detected</strong> — {lastAlert.new_count} new,{" "}
            {lastAlert.unresolved_critical_total} total unresolved critical.
          </span>
          <a href="/anomalies" style={{ color: "#991b1b", fontWeight: 600, fontSize: "0.8rem" }}>
            View &rarr;
          </a>
        </div>
      )}

      <div className="stat-grid">
        <StatCard
          label="API Status"
          value={isLoading ? "…" : apiOnline ? "Online" : "Offline"}
          accent={apiOnline ? "#008751" : "#dc2626"}
        />
        <StatCard
          label="Results Collected"
          value={isLoading ? "…" : (stats?.total_results ?? 0).toLocaleString()}
        />
        <StatCard
          label="Active Observers"
          value={isLoading ? "…" : (stats?.active_observers ?? 0).toLocaleString()}
        />
        <StatCard
          label="Open Anomalies"
          value={isLoading ? "…" : (stats?.open_anomalies ?? 0).toLocaleString()}
          accent={stats?.open_anomalies > 0 ? "#dc2626" : undefined}
        />
      </div>

      {scrape && (
        <div
          style={{
            marginTop: "1.5rem",
            padding: "0.75rem 1rem",
            background: "#f9fafb",
            border: "1px solid #e5e7eb",
            borderRadius: 6,
            fontSize: "0.8rem",
            color: "#6b7280",
          }}
        >
          <strong>Latest scrape:</strong>{" "}
          {scrape.status} — {scrape.images_downloaded ?? 0} images
          {scrape.completed_at
            ? ` · ${new Date(scrape.completed_at).toLocaleString("en-NG", {
                timeZone: "Africa/Lagos",
              })}`
            : " · in progress"}
        </div>
      )}

      <p style={{ marginTop: "1.5rem", color: "#6b7280", fontSize: "0.9rem" }}>
        Select an election from Results to begin monitoring.
      </p>
    </div>
  );
}

function StatCard({ label, value, accent }) {
  return (
    <div className="stat-card">
      <div className="stat-value" style={accent ? { color: accent } : {}}>
        {value}
      </div>
      <div className="stat-label">{label}</div>
    </div>
  );
}
