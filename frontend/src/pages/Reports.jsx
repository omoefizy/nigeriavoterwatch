import React, { useState, useEffect, useRef, useCallback } from "react";
import { Link } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { submitReport, getReports, searchPollingUnit } from "../services/api";
import { isLoggedIn, getRole } from "../services/auth";

const QUEUE_KEY = "nvw_report_queue";
const INCIDENTS = [
  { key: "violence_reported",          label: "Violence / intimidation" },
  { key: "ballot_stuffing_reported",   label: "Ballot stuffing" },
  { key: "underage_voting_reported",   label: "Underage voting" },
  { key: "security_personnel_present", label: "Security personnel present", defaultTrue: true },
  { key: "inec_officials_present",     label: "INEC officials present",     defaultTrue: true },
];

function genRef() {
  return "NVW-" + Date.now().toString(36).toUpperCase() + "-" + Math.random().toString(36).slice(2, 6).toUpperCase();
}

function readQueue() {
  try { return JSON.parse(localStorage.getItem(QUEUE_KEY) || "[]"); }
  catch { return []; }
}
function writeQueue(q) {
  localStorage.setItem(QUEUE_KEY, JSON.stringify(q));
}

const BLANK_FORM = {
  electionId: "",
  puCode: "",
  narrative: "",
  estimatedTurnout: "",
  photos: [],          // { url: objectUrl, file }
  location: null,
  ...Object.fromEntries(INCIDENTS.map((i) => [i.key, i.defaultTrue ?? false])),
};

export default function Reports() {
  const loggedIn = isLoggedIn();
  const userRole = getRole();

  const [form, setForm] = useState(BLANK_FORM);
  const [puResult, setPuResult] = useState(null);
  const [puError, setPuError] = useState("");
  const [gpsStatus, setGpsStatus] = useState("idle"); // idle | fetching | ok | denied
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState("");
  const [submitted, setSubmitted] = useState(null); // { ref }
  const [isOnline, setIsOnline] = useState(navigator.onLine);
  const [queuedCount, setQueuedCount] = useState(readQueue().length);
  const photoInputRef = useRef(null);

  // ── Flush offline queue when back online ──────────────────────────────────
  // Must be declared before the network-listener effect that calls it.
  const flushQueue = useCallback(async () => {
    const queue = readQueue();
    if (!queue.length) return;
    const failed = [];
    for (const item of queue) {
      try {
        await submitReport(item.payload);
      } catch {
        failed.push(item);
      }
    }
    writeQueue(failed);
    setQueuedCount(failed.length);
  }, []);

  // ── Network listener ──────────────────────────────────────────────────────
  useEffect(() => {
    const up   = () => { setIsOnline(true);  flushQueue(); };
    const down = () => setIsOnline(false);
    window.addEventListener("online",  up);
    window.addEventListener("offline", down);
    return () => { window.removeEventListener("online", up); window.removeEventListener("offline", down); };
  }, [flushQueue]);

  // ── PU lookup ──────────────────────────────────────────────────────────────
  async function lookupPU() {
    if (!form.puCode.trim()) return;
    setPuError("");
    setPuResult(null);
    try {
      const pu = await searchPollingUnit(form.puCode.trim());
      setPuResult(pu);
    } catch {
      setPuError("Polling unit not found. Check the code and try again.");
    }
  }

  // ── GPS capture ───────────────────────────────────────────────────────────
  function captureGPS() {
    if (!navigator.geolocation) {
      setGpsStatus("denied");
      return;
    }
    setGpsStatus("fetching");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        setForm((f) => ({
          ...f,
          location: {
            latitude: pos.coords.latitude,
            longitude: pos.coords.longitude,
            accuracy_meters: pos.coords.accuracy,
          },
        }));
        setGpsStatus("ok");
      },
      () => setGpsStatus("denied"),
      { enableHighAccuracy: true, timeout: 10_000 }
    );
  }

  // ── Photo handling ────────────────────────────────────────────────────────
  function onPhotoChange(e) {
    const files = [...e.target.files];
    if (form.photos.length + files.length > 5) {
      alert("Maximum 5 photos allowed.");
      return;
    }
    const newPhotos = files.map((file) => ({
      file,
      url: URL.createObjectURL(file),
      name: file.name,
    }));
    setForm((f) => ({ ...f, photos: [...f.photos, ...newPhotos] }));
    e.target.value = "";
  }

  function removePhoto(idx) {
    setForm((f) => {
      URL.revokeObjectURL(f.photos[idx].url);
      return { ...f, photos: f.photos.filter((_, i) => i !== idx) };
    });
  }

  // ── Build payload ─────────────────────────────────────────────────────────
  function buildPayload() {
    return {
      election_id: form.electionId,
      polling_unit_id: puResult?.id ?? "",
      narrative: form.narrative || null,
      estimated_turnout: form.estimatedTurnout ? parseInt(form.estimatedTurnout, 10) : null,
      violence_reported:          form.violence_reported,
      ballot_stuffing_reported:   form.ballot_stuffing_reported,
      underage_voting_reported:   form.underage_voting_reported,
      security_personnel_present: form.security_personnel_present,
      inec_officials_present:     form.inec_officials_present,
      location: form.location ?? null,
      // photo_paths: would be S3/CDN URLs after upload; stub with filenames
      photo_paths: form.photos.map((p) => p.name),
    };
  }

  // ── Submit ────────────────────────────────────────────────────────────────
  async function handleSubmit(e) {
    e.preventDefault();
    setSubmitError("");

    if (!form.electionId) return setSubmitError("Select an election.");
    if (!puResult)        return setSubmitError("Look up a valid polling unit first.");

    const payload = buildPayload();
    const ref     = genRef();

    setSubmitting(true);
    try {
      if (!navigator.onLine) throw new Error("offline");
      await submitReport(payload);
      setSubmitted({ ref });
      setForm(BLANK_FORM);
      setPuResult(null);
      setGpsStatus("idle");
    } catch (err) {
      if (!navigator.onLine || err?.message === "offline") {
        const queue = readQueue();
        queue.push({ payload, ref, queuedAt: new Date().toISOString() });
        writeQueue(queue);
        setQueuedCount(queue.length);
        setSubmitted({ ref, queued: true });
        setForm(BLANK_FORM);
        setPuResult(null);
        setGpsStatus("idle");
      } else {
        setSubmitError(err?.response?.data?.detail ?? "Submission failed. Please try again.");
      }
    } finally {
      setSubmitting(false);
    }
  }

  // ── Recent reports list ───────────────────────────────────────────────────
  const { data: reportsData } = useQuery({
    queryKey: ["reports"],
    queryFn: () => getReports({ page_size: 20 }),
    refetchInterval: 30_000,
  });
  const reports = reportsData?.items ?? [];

  return (
    <div>
      <div className="page-header">
        <h1>Observer Reports</h1>
        <p>Submit field reports from polling units. Works offline — reports queue until connectivity returns.</p>
      </div>

      {/* Offline banner */}
      {!isOnline && (
        <div className="offline-banner">
          <span>You are offline. Reports will be queued and sent when connectivity returns.</span>
          {queuedCount > 0 && <span><strong>{queuedCount}</strong> queued</span>}
        </div>
      )}

      {isOnline && queuedCount > 0 && (
        <div className="offline-banner" style={{ background: "#ecfdf5", borderColor: "#34d399", color: "#065f46" }}>
          <span>Back online — flushing {queuedCount} queued report(s)…</span>
        </div>
      )}

      <div className="reports-layout">
        {/* ── Submission form ── */}
        {!loggedIn ? (
          <div className="report-form">
            <h2>New Field Report</h2>
            <div className="auth-gate">
              <div className="auth-gate-icon">🔒</div>
              <p>You must be signed in as a verified observer to submit field reports.</p>
              <Link
                to={`/login?next=/reports`}
                className="btn btn-primary"
                style={{ display: "inline-block", textDecoration: "none", textAlign: "center" }}
              >
                Sign In
              </Link>
              <p style={{ fontSize: "0.78rem", color: "#9ca3af", marginTop: "0.5rem" }}>
                Don&apos;t have an account?{" "}
                <Link to="/login?tab=register" style={{ color: "#008751" }}>Register with invite code</Link>
              </p>
            </div>
          </div>
        ) : (
        <form className="report-form" onSubmit={handleSubmit} noValidate>
          <h2>New Field Report</h2>
          <div className="auth-role-badge">
            <span className={`badge ${userRole === "admin" ? "badge-blue" : "badge-green"}`}>
              {userRole === "admin" ? "Admin" : "Observer"}
            </span>
            <span style={{ fontSize: "0.75rem", color: "#6b7280" }}>Signed in — report will be attributed to your account</span>
          </div>

          {submitted && (
            <div className="success-banner">
              {submitted.queued
                ? <>Report queued. Reference: <span className="success-ref">{submitted.ref}</span> — will send when online.</>
                : <>Report submitted! Reference: <span className="success-ref">{submitted.ref}</span></>
              }
            </div>
          )}

          {submitError && <div className="error-msg">{submitError}</div>}

          <div className="form-group">
            <label>Election ID</label>
            <input
              type="text"
              placeholder="Paste election ID from Results page"
              value={form.electionId}
              onChange={(e) => setForm((f) => ({ ...f, electionId: e.target.value }))}
            />
          </div>

          {/* PU lookup */}
          <div className="form-group">
            <label>Polling Unit Code</label>
            <div style={{ display: "flex", gap: "0.5rem" }}>
              <input
                type="text"
                placeholder="e.g. 01/03/08/001"
                value={form.puCode}
                onChange={(e) => { setForm((f) => ({ ...f, puCode: e.target.value })); setPuResult(null); setPuError(""); }}
                style={{ flex: 1 }}
              />
              <button type="button" className="btn btn-ghost" onClick={lookupPU}>Verify</button>
            </div>
            {puError && <span style={{ color: "var(--danger)", fontSize: "0.78rem" }}>{puError}</span>}
            {puResult && (
              <div style={{ background: "#d1fae5", borderRadius: 4, padding: "0.4rem 0.6rem", fontSize: "0.78rem", color: "#065f46", marginTop: 4 }}>
                ✓ {puResult.name} — {puResult.registered_voters?.toLocaleString()} registered voters
              </div>
            )}
          </div>

          {/* GPS */}
          <div className="form-group">
            <label>GPS Location</label>
            <div className="gps-row">
              <button type="button" className="btn btn-ghost btn-sm" onClick={captureGPS} disabled={gpsStatus === "fetching"}>
                {gpsStatus === "fetching" ? "Locating…" : "Capture GPS"}
              </button>
              {gpsStatus === "ok" && form.location && (
                <span className="gps-value">
                  {form.location.latitude.toFixed(5)}, {form.location.longitude.toFixed(5)}
                  {form.location.accuracy_meters && ` ±${Math.round(form.location.accuracy_meters)}m`}
                </span>
              )}
              {gpsStatus === "denied" && (
                <span style={{ fontSize: "0.78rem", color: "var(--danger)" }}>Location access denied</span>
              )}
            </div>
          </div>

          {/* Incident checkboxes */}
          <div className="form-group">
            <label>Observations</label>
            <div className="checkbox-group">
              {INCIDENTS.map((inc) => (
                <label key={inc.key} className="checkbox-row">
                  <input
                    type="checkbox"
                    checked={!!form[inc.key]}
                    onChange={(e) => setForm((f) => ({ ...f, [inc.key]: e.target.checked }))}
                  />
                  {inc.label}
                </label>
              ))}
            </div>
          </div>

          <div className="form-group">
            <label>Estimated turnout (voters seen)</label>
            <input
              type="number"
              min="0"
              placeholder="Optional"
              value={form.estimatedTurnout}
              onChange={(e) => setForm((f) => ({ ...f, estimatedTurnout: e.target.value }))}
            />
          </div>

          <div className="form-group">
            <label>Narrative (optional)</label>
            <textarea
              placeholder="Describe what you observed…"
              value={form.narrative}
              onChange={(e) => setForm((f) => ({ ...f, narrative: e.target.value }))}
            />
          </div>

          {/* Photo upload */}
          <div className="form-group">
            <label>Photos (up to 5)</label>
            <button
              type="button"
              className="btn btn-ghost btn-sm"
              onClick={() => photoInputRef.current?.click()}
              disabled={form.photos.length >= 5}
            >
              Add photos
            </button>
            <input
              ref={photoInputRef}
              type="file"
              accept="image/*"
              multiple
              style={{ display: "none" }}
              onChange={onPhotoChange}
            />
            {form.photos.length > 0 && (
              <div className="photo-grid">
                {form.photos.map((p, i) => (
                  <div key={p.url} className="photo-thumb">
                    <img src={p.url} alt={`photo-${i}`} />
                    <button type="button" className="photo-remove" onClick={() => removePhoto(i)}>✕</button>
                  </div>
                ))}
              </div>
            )}
          </div>

          <button type="submit" className="btn btn-primary" disabled={submitting} style={{ width: "100%" }}>
            {submitting ? "Submitting…" : isOnline ? "Submit Report" : "Queue Report"}
          </button>
        </form>
        )}

        {/* ── Recent reports list ── */}
        <div className="report-list-panel">
          <h3>Recent Reports ({reportsData?.total ?? 0})</h3>
          {reports.length === 0 && <div className="empty-state">No reports submitted yet.</div>}
          {reports.map((r) => (
            <div key={r.id} className="report-item">
              <div className="report-item-header">
                <span className="td-mono" style={{ fontSize: "0.78rem" }}>
                  PU: {r.polling_unit_id.slice(-6)}
                </span>
                <div style={{ display: "flex", gap: "0.35rem" }}>
                  {r.violence_reported && <span className="badge badge-red">Violence</span>}
                  {r.ballot_stuffing_reported && <span className="badge badge-red">Stuffing</span>}
                  {!r.violence_reported && !r.ballot_stuffing_reported && (
                    <span className="badge badge-green">No incidents</span>
                  )}
                </div>
              </div>
              <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>
                {new Date(r.created_at).toLocaleString("en-NG", { timeZone: "Africa/Lagos" })}
                {r.estimated_turnout && ` · ~${r.estimated_turnout} voters`}
              </div>
              {r.narrative && (
                <div style={{ marginTop: "0.3rem", fontSize: "0.8rem", color: "#374151" }}>
                  {r.narrative.length > 120 ? r.narrative.slice(0, 120) + "…" : r.narrative}
                </div>
              )}
            </div>
          ))}

          {/* Queued offline reports */}
          {queuedCount > 0 && (
            <>
              <h3 style={{ marginTop: "1rem" }}>Queued Offline ({queuedCount})</h3>
              {readQueue().map((item) => (
                <div key={item.ref} className="report-item" style={{ borderColor: "#f59e0b" }}>
                  <div className="report-item-header">
                    <span className="td-mono" style={{ fontSize: "0.78rem" }}>{item.ref}</span>
                    <span className="badge badge-yellow">Pending</span>
                  </div>
                  <div style={{ color: "#6b7280", fontSize: "0.75rem" }}>
                    Queued {new Date(item.queuedAt).toLocaleString("en-NG", { timeZone: "Africa/Lagos" })}
                  </div>
                </div>
              ))}
            </>
          )}
        </div>
      </div>
    </div>
  );
}
