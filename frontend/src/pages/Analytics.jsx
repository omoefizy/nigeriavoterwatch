import React from "react";
import { useQuery } from "@tanstack/react-query";
import {
  BarChart, Bar, XAxis, YAxis, Tooltip, CartesianGrid, ResponsiveContainer, Legend,
  LineChart, Line,
  ScatterChart, Scatter, ZAxis,
  Cell,
} from "recharts";
import { format } from "date-fns";
import { getUploadByState, getScrapeHistory, getAnomalySummary } from "../services/api";

const GREEN  = "#008751";
const YELLOW = "#f59e0b";
const RED    = "#dc2626";
const BLUE   = "#3b82f6";

// Severity colours for scatter
const SEV_COLOR = { critical: RED, warning: YELLOW, info: BLUE };

function ChartCard({ title, children, fullWidth }) {
  return (
    <div className={`chart-card${fullWidth ? " full-width" : ""}`}>
      <h3>{title}</h3>
      {children}
    </div>
  );
}

function EmptyChart({ message = "No data yet" }) {
  return <div className="empty-state" style={{ padding: "2rem" }}>{message}</div>;
}

// ── Upload coverage BarChart ──────────────────────────────────────────────────

function UploadByStateChart() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["upload-by-state"],
    queryFn: getUploadByState,
    refetchInterval: 60_000,
  });

  if (isLoading) return <div className="loading">Loading…</div>;
  if (isError)   return <EmptyChart message="Could not load upload data" />;
  if (!data?.length) return <EmptyChart />;

  // Sort by upload_pct desc, cap at 30 states for readability
  const sorted = [...data].sort((a, b) => b.upload_pct - a.upload_pct).slice(0, 37);

  return (
    <ResponsiveContainer width="100%" height={340}>
      <BarChart data={sorted} margin={{ top: 4, right: 16, left: 0, bottom: 80 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis
          dataKey="state_name"
          tick={{ fontSize: 10 }}
          angle={-45}
          textAnchor="end"
          interval={0}
        />
        <YAxis
          domain={[0, 100]}
          tickFormatter={(v) => `${v}%`}
          tick={{ fontSize: 11 }}
          width={40}
        />
        <Tooltip formatter={(v) => [`${v}%`, "Upload %"]} />
        <Bar dataKey="upload_pct" name="Upload %" radius={[3, 3, 0, 0]}>
          {sorted.map((entry) => (
            <Cell
              key={entry.state_name}
              fill={entry.upload_pct >= 75 ? GREEN : entry.upload_pct >= 40 ? YELLOW : RED}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  );
}

// ── Scrape history LineChart ──────────────────────────────────────────────────

function ScrapeHistoryChart() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["scrape-history"],
    queryFn: () => getScrapeHistory(96),
    refetchInterval: 60_000,
  });

  if (isLoading) return <div className="loading">Loading…</div>;
  if (isError)   return <EmptyChart message="Could not load scrape history" />;
  if (!data?.length) return <EmptyChart message="No scrape runs recorded yet" />;

  // Chronological order; keep last 48 points for readability
  const sorted = [...data]
    .sort((a, b) => new Date(a.started_at) - new Date(b.started_at))
    .slice(-48)
    .map((r) => ({
      time: format(new Date(r.started_at), "HH:mm"),
      downloaded: r.images_downloaded,
      missing: r.missing_url_count,
      total: r.total_visited,
    }));

  return (
    <ResponsiveContainer width="100%" height={280}>
      <LineChart data={sorted} margin={{ top: 4, right: 16, left: 0, bottom: 4 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} />
        <XAxis dataKey="time" tick={{ fontSize: 10 }} interval="preserveStartEnd" />
        <YAxis tick={{ fontSize: 11 }} width={48} />
        <Tooltip />
        <Legend iconSize={10} wrapperStyle={{ fontSize: "0.78rem" }} />
        <Line type="monotone" dataKey="downloaded" name="Images downloaded" stroke={GREEN}  dot={false} strokeWidth={2} />
        <Line type="monotone" dataKey="missing"    name="Missing URLs"      stroke={RED}    dot={false} strokeWidth={1.5} strokeDasharray="4 2" />
        <Line type="monotone" dataKey="total"      name="PUs visited"       stroke={BLUE}   dot={false} strokeWidth={1.5} strokeDasharray="2 2" />
      </LineChart>
    </ResponsiveContainer>
  );
}

// ── Anomaly scatter by state ──────────────────────────────────────────────────

function AnomalyScatterChart() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["anomaly-summary"],
    queryFn: getAnomalySummary,
    refetchInterval: 120_000,
  });

  if (isLoading) return <div className="loading">Loading…</div>;
  if (isError)   return <EmptyChart message="Could not load anomaly data" />;
  if (!data?.length) return <EmptyChart message="No anomalies recorded" />;

  // Build scatter data: one point per state × severity
  const points = [];
  data.forEach((s, stateIdx) => {
    ["critical", "warning", "info"].forEach((sev) => {
      if (s[sev] > 0) {
        points.push({ x: stateIdx, y: s[sev], z: s[sev], state: s.state_name, severity: sev });
      }
    });
  });

  const stateLabels = data.map((s) => s.state_name);

  return (
    <ResponsiveContainer width="100%" height={340}>
      <ScatterChart margin={{ top: 4, right: 24, left: 0, bottom: 80 }}>
        <CartesianGrid strokeDasharray="3 3" />
        <XAxis
          type="number"
          dataKey="x"
          domain={[-1, stateLabels.length]}
          tickFormatter={(v) => stateLabels[v] ?? ""}
          tick={{ fontSize: 9 }}
          angle={-45}
          textAnchor="end"
          interval={0}
        />
        <YAxis type="number" dataKey="y" name="Count" tick={{ fontSize: 11 }} width={36} />
        <ZAxis type="number" dataKey="z" range={[40, 400]} />
        <Tooltip
          cursor={{ strokeDasharray: "3 3" }}
          content={({ payload }) => {
            if (!payload?.length) return null;
            const d = payload[0].payload;
            return (
              <div style={{ background: "#fff", border: "1px solid #e5e7eb", borderRadius: 6, padding: "0.5rem 0.75rem", fontSize: "0.8rem" }}>
                <div><strong>{d.state}</strong></div>
                <div>{d.severity}: {d.y}</div>
              </div>
            );
          }}
        />
        {["critical", "warning", "info"].map((sev) => (
          <Scatter
            key={sev}
            name={sev.charAt(0).toUpperCase() + sev.slice(1)}
            data={points.filter((p) => p.severity === sev)}
            fill={SEV_COLOR[sev]}
            fillOpacity={0.75}
          />
        ))}
        <Legend iconType="circle" iconSize={10} wrapperStyle={{ fontSize: "0.78rem" }} />
      </ScatterChart>
    </ResponsiveContainer>
  );
}

// ── Summary table ─────────────────────────────────────────────────────────────

function AnomalySummaryTable() {
  const { data, isLoading } = useQuery({
    queryKey: ["anomaly-summary"],
    queryFn: getAnomalySummary,
  });

  if (isLoading) return <div className="loading">Loading…</div>;
  if (!data?.length) return null;

  const topStates = [...data].sort((a, b) => b.unresolved - a.unresolved).slice(0, 10);

  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th>State</th>
            <th>Critical</th>
            <th>Warning</th>
            <th>Info</th>
            <th>Total</th>
            <th>Unresolved</th>
          </tr>
        </thead>
        <tbody>
          {topStates.map((s) => (
            <tr key={s.state_name}>
              <td>{s.state_name}</td>
              <td>{s.critical > 0 ? <span className="badge badge-red">{s.critical}</span> : "—"}</td>
              <td>{s.warning  > 0 ? <span className="badge badge-yellow">{s.warning}</span> : "—"}</td>
              <td>{s.info     > 0 ? <span className="badge badge-blue">{s.info}</span> : "—"}</td>
              <td><strong>{s.total}</strong></td>
              <td>{s.unresolved > 0 ? <span className="badge badge-red">{s.unresolved}</span> : <span className="badge badge-green">0</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ── Page ──────────────────────────────────────────────────────────────────────

export default function Analytics() {
  return (
    <div>
      <div className="page-header">
        <h1>Analytics</h1>
        <p>Upload coverage, scrape cadence, and anomaly distribution across Nigeria.</p>
      </div>

      <div className="charts-grid">
        <ChartCard title="EC8A Upload Coverage by State" fullWidth>
          <UploadByStateChart />
        </ChartCard>

        <ChartCard title="IReV Scrape Rate Over Time (last 48 runs)">
          <ScrapeHistoryChart />
        </ChartCard>

        <ChartCard title="Anomaly Distribution by State and Severity">
          <AnomalyScatterChart />
        </ChartCard>
      </div>

      <div style={{ marginTop: "1.25rem" }}>
        <h3 style={{ marginBottom: "0.75rem", fontSize: "0.95rem", fontWeight: 700 }}>
          Top States by Unresolved Anomalies
        </h3>
        <AnomalySummaryTable />
      </div>
    </div>
  );
}
