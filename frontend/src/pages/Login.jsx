import React, { useState } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { login } from "../services/auth";
import { api } from "../services/api";

const ORGS = [
  { value: "individual",  label: "Individual Observer" },
  { value: "yiaga",       label: "YIAGA Africa" },
  { value: "tmg",         label: "Transition Monitoring Group" },
  { value: "cdd",         label: "Centre for Democracy & Development" },
  { value: "eu",          label: "EU Election Observation Mission" },
  { value: "au",          label: "African Union Observer Mission" },
  { value: "ecowas",      label: "ECOWAS Observer Mission" },
  { value: "other",       label: "Other" },
];

export default function Login() {
  const [tab, setTab] = useState("login");
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const next = searchParams.get("next") || "/";

  function switchTab(t) {
    setTab(t);
    setLoginError("");
    setRegError("");
  }

  // ── Login state ──────────────────────────────────────────────────────────────
  const [loginEmail,    setLoginEmail]    = useState("");
  const [loginPassword, setLoginPassword] = useState("");
  const [loginError,    setLoginError]    = useState("");
  const [loginLoading,  setLoginLoading]  = useState(false);

  async function handleLogin(e) {
    e.preventDefault();
    setLoginError("");
    setLoginLoading(true);
    try {
      await login(loginEmail.trim(), loginPassword);
      navigate(next, { replace: true });
    } catch (err) {
      setLoginError(err?.response?.data?.detail ?? "Login failed. Check your credentials.");
    } finally {
      setLoginLoading(false);
    }
  }

  // ── Register state ───────────────────────────────────────────────────────────
  const BLANK_REG = {
    email: "", invite_code: "", full_name: "",
    password: "", confirmPassword: "",
    phone: "", organization: "individual", accreditation_id: "",
  };
  const [reg,        setReg]        = useState(BLANK_REG);
  const [regError,   setRegError]   = useState("");
  const [regLoading, setRegLoading] = useState(false);

  function setRegField(field) {
    return (e) => setReg((r) => ({ ...r, [field]: e.target.value }));
  }

  async function handleRegister(e) {
    e.preventDefault();
    setRegError("");

    if (reg.password !== reg.confirmPassword) {
      return setRegError("Passwords do not match.");
    }
    if (reg.password.length < 8) {
      return setRegError("Password must be at least 8 characters.");
    }

    setRegLoading(true);
    try {
      const payload = {
        email:            reg.email.trim().toLowerCase(),
        invite_code:      reg.invite_code.trim(),
        full_name:        reg.full_name.trim(),
        password:         reg.password,
        organization:     reg.organization,
      };
      if (reg.phone.trim())             payload.phone             = reg.phone.trim();
      if (reg.accreditation_id.trim())  payload.accreditation_id  = reg.accreditation_id.trim();

      await api.post("/api/observers/register", payload);
      // Auto-login with the newly created credentials
      await login(payload.email, reg.password);
      navigate(next, { replace: true });
    } catch (err) {
      const detail = err?.response?.data?.detail;
      if (Array.isArray(detail)) {
        setRegError(detail.map((d) => d.msg).join("; "));
      } else {
        setRegError(detail ?? "Registration failed. Check your invite code and try again.");
      }
    } finally {
      setRegLoading(false);
    }
  }

  return (
    <div style={{ display: "flex", justifyContent: "center", paddingTop: "4rem", paddingBottom: "4rem" }}>
      <div style={{
        background: "#fff",
        border: "1px solid #e5e7eb",
        borderRadius: 8,
        width: "100%",
        maxWidth: 460,
        overflow: "hidden",
        boxShadow: "0 1px 6px rgba(0,0,0,0.06)",
      }}>

        {/* Header */}
        <div style={{ padding: "1.5rem 2rem 0" }}>
          <div style={{ display: "flex", alignItems: "center", gap: "0.6rem", marginBottom: "0.25rem" }}>
            <div style={{
              width: 28, height: 28, borderRadius: 6,
              background: "#008751",
              display: "flex", alignItems: "center", justifyContent: "center",
              fontSize: "0.8rem", color: "#fff", fontWeight: 700,
            }}>NV</div>
            <span style={{ fontWeight: 700, fontSize: "1rem", color: "#111827" }}>NigeriaVoteWatch</span>
          </div>
          <p style={{ margin: "0 0 1.25rem", fontSize: "0.78rem", color: "#6b7280" }}>
            Election integrity monitoring platform
          </p>
        </div>

        {/* Tabs */}
        <div style={{ display: "flex", borderBottom: "2px solid #e5e7eb" }}>
          {[["login", "Sign In"], ["register", "Register as Observer"]].map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => switchTab(key)}
              style={{
                flex: 1,
                padding: "0.65rem 0.5rem",
                background: "none",
                border: "none",
                borderBottom: tab === key ? "2px solid #008751" : "2px solid transparent",
                marginBottom: -2,
                cursor: "pointer",
                fontSize: "0.84rem",
                fontWeight: tab === key ? 700 : 400,
                color: tab === key ? "#008751" : "#6b7280",
                transition: "color 0.15s, border-color 0.15s",
              }}
            >
              {label}
            </button>
          ))}
        </div>

        <div style={{ padding: "1.5rem 2rem 2rem" }}>
          {tab === "login" ? (
            // ── Login form ──────────────────────────────────────────────────────
            <form onSubmit={handleLogin} style={{ display: "flex", flexDirection: "column", gap: "0.875rem" }}>
              {loginError && <div className="error-msg">{loginError}</div>}

              <div className="form-group">
                <label>Email</label>
                <input
                  type="email"
                  autoComplete="email"
                  required
                  value={loginEmail}
                  onChange={(e) => setLoginEmail(e.target.value)}
                  placeholder="you@example.com"
                />
              </div>

              <div className="form-group">
                <label>Password</label>
                <input
                  type="password"
                  autoComplete="current-password"
                  required
                  value={loginPassword}
                  onChange={(e) => setLoginPassword(e.target.value)}
                  placeholder="••••••••"
                />
              </div>

              <button
                type="submit"
                className="btn btn-primary"
                disabled={loginLoading}
                style={{ width: "100%", marginTop: "0.25rem", padding: "0.6rem" }}
              >
                {loginLoading ? "Signing in…" : "Sign In"}
              </button>

              <p style={{ textAlign: "center", fontSize: "0.78rem", color: "#6b7280", margin: 0 }}>
                Have an invite code?{" "}
                <button
                  type="button"
                  onClick={() => switchTab("register")}
                  style={{
                    background: "none", border: "none", color: "#008751",
                    cursor: "pointer", fontSize: "inherit", fontWeight: 600, padding: 0,
                  }}
                >
                  Register as observer
                </button>
              </p>
            </form>
          ) : (
            // ── Register form ───────────────────────────────────────────────────
            <form onSubmit={handleRegister} style={{ display: "flex", flexDirection: "column", gap: "0.875rem" }}>
              <p style={{ margin: 0, fontSize: "0.8rem", color: "#6b7280", lineHeight: 1.5 }}>
                Enter the invite code sent by the platform administrator, then
                choose a password to activate your observer account.
              </p>

              {regError && <div className="error-msg">{regError}</div>}

              {/* Required fields */}
              <div className="form-group">
                <label>Email <span style={{ color: "var(--danger)" }}>*</span></label>
                <input
                  type="email"
                  required
                  autoComplete="email"
                  value={reg.email}
                  onChange={setRegField("email")}
                  placeholder="you@example.com"
                />
              </div>

              <div className="form-group">
                <label>Invite Code <span style={{ color: "var(--danger)" }}>*</span></label>
                <input
                  type="text"
                  required
                  autoComplete="off"
                  spellCheck={false}
                  value={reg.invite_code}
                  onChange={setRegField("invite_code")}
                  placeholder="Paste the code from your invitation email"
                  style={{ fontFamily: "monospace", letterSpacing: "0.03em" }}
                />
              </div>

              <div className="form-group">
                <label>Full Name <span style={{ color: "var(--danger)" }}>*</span></label>
                <input
                  type="text"
                  required
                  autoComplete="name"
                  value={reg.full_name}
                  onChange={setRegField("full_name")}
                  placeholder="Your full legal name"
                />
              </div>

              <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem" }}>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label>Password <span style={{ color: "var(--danger)" }}>*</span></label>
                  <input
                    type="password"
                    required
                    autoComplete="new-password"
                    value={reg.password}
                    onChange={setRegField("password")}
                    placeholder="≥ 8 characters"
                  />
                </div>
                <div className="form-group" style={{ marginBottom: 0 }}>
                  <label>Confirm Password <span style={{ color: "var(--danger)" }}>*</span></label>
                  <input
                    type="password"
                    required
                    autoComplete="new-password"
                    value={reg.confirmPassword}
                    onChange={setRegField("confirmPassword")}
                    placeholder="Repeat password"
                  />
                </div>
              </div>

              {/* Optional fields */}
              <div style={{ borderTop: "1px solid #f3f4f6", paddingTop: "0.75rem" }}>
                <p style={{ margin: "0 0 0.75rem", fontSize: "0.75rem", color: "#9ca3af", fontWeight: 600, textTransform: "uppercase", letterSpacing: "0.05em" }}>
                  Optional details
                </p>
                <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label>Phone</label>
                    <input
                      type="tel"
                      autoComplete="tel"
                      value={reg.phone}
                      onChange={setRegField("phone")}
                      placeholder="+234 800 000 0000"
                    />
                  </div>

                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label>Organization</label>
                    <select value={reg.organization} onChange={setRegField("organization")}>
                      {ORGS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </div>

                  <div className="form-group" style={{ marginBottom: 0 }}>
                    <label>INEC Accreditation ID</label>
                    <input
                      type="text"
                      value={reg.accreditation_id}
                      onChange={setRegField("accreditation_id")}
                      placeholder="e.g. ACC-2027-00001"
                    />
                  </div>
                </div>
              </div>

              <button
                type="submit"
                className="btn btn-primary"
                disabled={regLoading}
                style={{ width: "100%", padding: "0.6rem" }}
              >
                {regLoading ? "Activating account…" : "Activate Observer Account"}
              </button>

              <p style={{ textAlign: "center", fontSize: "0.78rem", color: "#6b7280", margin: 0 }}>
                Already registered?{" "}
                <button
                  type="button"
                  onClick={() => switchTab("login")}
                  style={{
                    background: "none", border: "none", color: "#008751",
                    cursor: "pointer", fontSize: "inherit", fontWeight: 600, padding: 0,
                  }}
                >
                  Sign in
                </button>
              </p>
            </form>
          )}
        </div>
      </div>
    </div>
  );
}
