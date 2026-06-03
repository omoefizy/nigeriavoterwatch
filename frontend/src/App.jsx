import React, { useEffect, useCallback } from "react";
import { Routes, Route, NavLink, useNavigate, useLocation } from "react-router-dom";
import Home from "./pages/Home";
import Results from "./pages/Results";
import MapPage from "./pages/MapPage";
import Reports from "./pages/Reports";
import Analytics from "./pages/Analytics";
import Anomalies from "./pages/Anomalies";
import Login from "./pages/Login";
import { isLoggedIn, getRole, logout } from "./services/auth";
import { api } from "./services/api";
import { useResultsSocket } from "./hooks/useResultsSocket";
import { useWsStore } from "./store/wsStore";

export default function App() {
  const navigate = useNavigate();
  const location = useLocation();
  const loggedIn = isLoggedIn();
  const role = getRole();

  const { handleMessage, setConnected, setCriticalCount, criticalCount } = useWsStore();

  // Fetch initial unresolved CRITICAL count on mount
  useEffect(() => {
    api
      .get("/api/anomalies", {
        params: { severity: "critical", is_resolved: false, page_size: 1 },
      })
      .then((r) => setCriticalCount(r.data.total ?? 0))
      .catch(() => {});
  }, [setCriticalCount]);

  // Single WebSocket connection for the entire app
  const onOpen  = useCallback(() => setConnected(true),  [setConnected]);
  const onClose = useCallback(() => setConnected(false), [setConnected]);
  useResultsSocket(handleMessage, { onOpen, onClose });

  function handleLogout() {
    logout();
    navigate("/login");
  }

  return (
    <div className="app">
      <nav className="navbar">
        <span className="navbar-brand">NigeriaVoteWatch</span>
        <div className="nav-links">
          <NavLink to="/">Dashboard</NavLink>
          <NavLink to="/results">Results</NavLink>
          <NavLink to="/map">Map</NavLink>
          <NavLink to="/reports">Reports</NavLink>
          <NavLink to="/analytics">Analytics</NavLink>
          <NavLink to="/anomalies" style={{ position: "relative" }}>
            Anomalies
            {criticalCount > 0 && (
              <span className="nav-badge-pulse">
                {criticalCount > 99 ? "99+" : criticalCount}
              </span>
            )}
          </NavLink>
        </div>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: "0.75rem" }}>
          {loggedIn ? (
            <>
              <span style={{ fontSize: "0.78rem", color: "#6b7280", textTransform: "capitalize" }}>
                {role}
              </span>
              <button onClick={handleLogout} className="btn btn-ghost btn-sm">
                Sign out
              </button>
            </>
          ) : (
            <NavLink
              to={`/login${location.pathname !== "/login" ? `?next=${encodeURIComponent(location.pathname)}` : ""}`}
              className="btn btn-ghost btn-sm"
            >
              Sign in
            </NavLink>
          )}
        </div>
      </nav>
      <main>
        <Routes>
          <Route path="/"          element={<Home />} />
          <Route path="/results"   element={<Results />} />
          <Route path="/map"       element={<MapPage />} />
          <Route path="/reports"   element={<Reports />} />
          <Route path="/analytics" element={<Analytics />} />
          <Route path="/anomalies" element={<Anomalies />} />
          <Route path="/login"     element={<Login />} />
        </Routes>
      </main>
    </div>
  );
}
