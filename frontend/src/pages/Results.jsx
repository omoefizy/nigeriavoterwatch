import React, { useState, useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { getElections, getResults, getStates, getLGAs, getWards } from "../services/api";

const STATUS_BADGE = {
  preliminary: "badge-yellow",
  verified: "badge-green",
  disputed: "badge-red",
  superseded: "badge-gray",
};

export default function Results() {
  const [electionId, setElectionId] = useState("");
  const [stateId, setStateId] = useState("");
  const [lgaId, setLgaId] = useState("");
  const [wardId, setWardId] = useState("");
  const [puSearch, setPuSearch] = useState("");
  const [page, setPage] = useState(1);

  // Elections for selector
  const { data: electionsData } = useQuery({
    queryKey: ["elections"],
    queryFn: () => getElections({ page_size: 100 }),
  });
  const elections = electionsData?.items ?? [];

  // Geographic cascade
  const { data: states = [] } = useQuery({
    queryKey: ["states"],
    queryFn: getStates,
  });
  const { data: lgas = [] } = useQuery({
    queryKey: ["lgas", stateId],
    queryFn: () => getLGAs(stateId),
    enabled: !!stateId,
  });
  const { data: wards = [] } = useQuery({
    queryKey: ["wards", lgaId],
    queryFn: () => getWards(lgaId),
    enabled: !!lgaId,
  });

  // Build query params
  const params = useMemo(() => {
    const p = { page, page_size: 50 };
    if (electionId) p.election_id = electionId;
    if (wardId) p.ward_id = wardId;
    else if (lgaId) p.lga_id = lgaId;
    if (puSearch.trim()) p.polling_unit_id = puSearch.trim();
    return p;
  }, [electionId, lgaId, wardId, puSearch, page]);

  const { data, isLoading, isError } = useQuery({
    queryKey: ["results", params],
    queryFn: () => getResults(params),
    keepPreviousData: true,
    enabled: !!electionId,
  });

  const results = data?.items ?? [];
  const total = data?.total ?? 0;
  const pages = data?.pages ?? 1;

  // Derive all party names in this page's results
  const partyColumns = useMemo(() => {
    const parties = new Set();
    results.forEach((r) => Object.keys(r.party_votes ?? {}).forEach((p) => parties.add(p)));
    return [...parties].sort();
  }, [results]);

  function handleStateChange(e) {
    setStateId(e.target.value);
    setLgaId("");
    setWardId("");
    setPage(1);
  }
  function handleLgaChange(e) {
    setLgaId(e.target.value);
    setWardId("");
    setPage(1);
  }
  function handleWardChange(e) {
    setWardId(e.target.value);
    setPage(1);
  }

  return (
    <div>
      <div className="page-header">
        <h1>Election Results</h1>
        <p>Polling-unit level results with cryptographic chain verification.</p>
      </div>

      {/* Election selector */}
      <div className="election-selector">
        <label>Election</label>
        <select value={electionId} onChange={(e) => { setElectionId(e.target.value); setPage(1); }}>
          <option value="">— Select an election —</option>
          {elections.map((e) => (
            <option key={e.id} value={e.id}>
              {e.name} ({new Date(e.election_date).getFullYear()}) — {e.status}
            </option>
          ))}
        </select>
      </div>

      {/* Filters */}
      <div className="filter-bar">
        <label>
          State
          <select value={stateId} onChange={handleStateChange}>
            <option value="">All states</option>
            {states.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </label>

        <label>
          LGA
          <select value={lgaId} onChange={handleLgaChange} disabled={!stateId}>
            <option value="">All LGAs</option>
            {lgas.map((l) => <option key={l.id} value={l.id}>{l.name}</option>)}
          </select>
        </label>

        <label>
          Ward
          <select value={wardId} onChange={handleWardChange} disabled={!lgaId}>
            <option value="">All wards</option>
            {wards.map((w) => <option key={w.id} value={w.id}>{w.name}</option>)}
          </select>
        </label>

        <label>
          Search PU code
          <input
            type="search"
            placeholder="e.g. 01/01/01/001"
            value={puSearch}
            onChange={(e) => { setPuSearch(e.target.value); setPage(1); }}
          />
        </label>

        <button
          className="btn btn-ghost"
          onClick={() => { setStateId(""); setLgaId(""); setWardId(""); setPuSearch(""); setPage(1); }}
        >
          Clear
        </button>
      </div>

      {!electionId && (
        <div className="empty-state">Select an election above to load results.</div>
      )}

      {electionId && isError && (
        <div className="error-msg">Failed to load results. Check API connection.</div>
      )}

      {electionId && !isError && (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th>PU Code</th>
                <th>PU Name</th>
                <th>State</th>
                <th>LGA</th>
                <th>Ward</th>
                {partyColumns.map((p) => <th key={p}>{p}</th>)}
                <th>Accredited</th>
                <th>Votes Cast</th>
                <th>Status</th>
                <th>Chain #</th>
              </tr>
            </thead>
            <tbody>
              {isLoading && (
                <tr><td colSpan={10 + partyColumns.length} className="loading">Loading…</td></tr>
              )}
              {!isLoading && results.length === 0 && (
                <tr><td colSpan={10 + partyColumns.length} className="empty-state">No results match filters.</td></tr>
              )}
              {results.map((r) => (
                <tr key={r.id}>
                  <td className="td-mono">{r.inec_pu_code ?? r.polling_unit_id.slice(-6)}</td>
                  <td>{r.pu_name ?? "—"}</td>
                  <td>{r.state_name ?? "—"}</td>
                  <td>{r.lga_name ?? "—"}</td>
                  <td>{r.ward_name ?? "—"}</td>
                  {partyColumns.map((p) => (
                    <td key={p}>{(r.party_votes?.[p] ?? 0).toLocaleString()}</td>
                  ))}
                  <td>{r.accredited_voters.toLocaleString()}</td>
                  <td>{r.total_votes_cast.toLocaleString()}</td>
                  <td>
                    <span className={`badge ${STATUS_BADGE[r.status] ?? "badge-gray"}`}>
                      {r.status}
                    </span>
                  </td>
                  <td className="td-mono">{r.chain_sequence}</td>
                </tr>
              ))}
            </tbody>
          </table>

          <div className="pagination">
            <span>
              {total.toLocaleString()} results — page {page} of {pages}
            </span>
            <div className="pagination-controls">
              <button className="btn btn-ghost btn-sm" onClick={() => setPage(1)} disabled={page === 1}>«</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setPage((p) => p - 1)} disabled={page === 1}>‹</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setPage((p) => p + 1)} disabled={page >= pages}>›</button>
              <button className="btn btn-ghost btn-sm" onClick={() => setPage(pages)} disabled={page >= pages}>»</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
