import React, { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../services/api";
import { useWsStore } from "../store/wsStore";

const SEVERITY_OPTS = [
  { value: "",         label: "All severities" },
  { value: "critical", label: "Critical" },
  { value: "warning",  label: "Warning" },
  { value: "info",     label: "Info" },
];

const TYPE_OPTS = [
  { value: "",                    label: "All types" },
  { value: "vote_inflation",      label: "Vote inflation" },
  { value: "overcreditation",     label: "Overcreditation" },
  { value: "unanimous_result",    label: "Unanimous result" },
  { value: "late_result",         label: "Late upload" },
  { value: "turnout_anomaly",     label: "Turnout anomaly" },
  { value: "image_hash_changed",  label: "Image hash changed" },
  { value: "result_discrepancy",  label: "OCR discrepancy" },
];

const RESOLVED_OPTS = [
  { value: "",      label: "All statuses" },
  { value: "false", label: "Open" },
  { value: "true",  label: "Resolved" },
];

const PAGE_SIZE = 50;

export default function Anomalies() {
  const [severity,   setSeverity]   = useState("");
  const [type,       setType]       = useState("");
  const [isResolved, setIsResolved] = useState("false");
  const [page,       setPage]       = useState(1);

  const { lastAlert } = useWsStore();

  const params = { page, page_size: PAGE_SIZE };
  if (severity)   params.severity   = severity;
  if (type)       params.anomaly_type = type;
  if (isResolved !== "") params.is_resolved = isResolved;

  const { data, isLoading } = useQuery({
    queryKey: ["anomalies", params],
    queryFn: () => api.get("/api/anomalies", { params }).then((r) => r.data),
    refetchInterval: 30_000,
  });

  const items = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = data?.pages ?? 1;

  function resetPage() { setPage(1); }

  return (
    <div>
      <div className="page-header">
        <h1>Anomalies</h1>
        <p>Integrity flags detected by the seven automated checks (C1–C7).</p>
      </div>

      {lastAlert && (
        <div className="error-msg" style={{ marginBottom: "1rem" }}>
          <strong>{lastAlert.new_count} new critical anomaly{lastAlert.new_count !== 1 ? "ies" : ""}</strong> detected
          just now — {lastAlert.unresolved_critical_total} unresolved critical total.
        </div>
      )}

      <div className="filter-bar">
        <label>
          Severity
          <select value={severity} onChange={(e) => { setSeverity(e.target.value); resetPage(); }}>
            {SEVERITY_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label>
          Type
          <select value={type} onChange={(e) => { setType(e.target.value); resetPage(); }}>
            {TYPE_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
        <label>
          Status
          <select value={isResolved} onChange={(e) => { setIsResolved(e.target.value); resetPage(); }}>
            {RESOLVED_OPTS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
          </select>
        </label>
      </div>

      {isLoading ? (
        <div className="loading">Loading anomalies…</div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Severity</th>
                <th>Type</th>
                <th>Description</th>
                <th>State</th>
                <th>LGA</th>
                <th>Detected</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {items.length === 0 ? (
                <tr>
                  <td colSpan={7} style={{ textAlign: "center", padding: "2rem", color: "#9ca3af" }}>
                    No anomalies match the current filters.
                  </td>
                </tr>
              ) : items.map((a) => (
                <tr key={a.id}>
                  <td><SeverityBadge s={a.severity} /></td>
                  <td className="td-mono">{a.anomaly_type}</td>
                  <td style={{ maxWidth: 380, whiteSpace: "normal", lineHeight: 1.4 }}>
                    {a.description}
                  </td>
                  <td>{a.state_name ?? "—"}</td>
                  <td>{a.lga_name ?? "—"}</td>
                  <td className="td-mono">
                    {new Date(a.detected_at).toLocaleString("en-NG", { timeZone: "Africa/Lagos" })}
                  </td>
                  <td>
                    {a.is_resolved ? (
                      <span className="badge badge-green">Resolved</span>
                    ) : (
                      <span className="badge badge-red">Open</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="pagination">
            <span>{total.toLocaleString()} anomal{total === 1 ? "y" : "ies"}</span>
            <div className="pagination-controls">
              <button
                className="btn btn-ghost btn-sm"
                disabled={page === 1}
                onClick={() => setPage((p) => p - 1)}
              >
                Prev
              </button>
              <span style={{ padding: "0.3rem 0.5rem", fontSize: "0.8rem" }}>
                {page} / {pages}
              </span>
              <button
                className="btn btn-ghost btn-sm"
                disabled={page >= pages}
                onClick={() => setPage((p) => p + 1)}
              >
                Next
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function SeverityBadge({ s }) {
  if (s === "critical") return <span className="badge badge-red">Critical</span>;
  if (s === "warning")  return <span className="badge badge-yellow">Warning</span>;
  return <span className="badge badge-gray">Info</span>;
}
