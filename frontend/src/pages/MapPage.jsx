import React, { useState, useEffect, useMemo } from "react";
import { MapContainer, TileLayer, GeoJSON, useMap } from "react-leaflet";
import { useQuery } from "@tanstack/react-query";
import { getElections, getUploadByState, getLGAs } from "../services/api";

// ── Colour scale: red (0%) → amber (50%) → green (100%) ────────────────────
function pctToColor(pct) {
  if (pct == null) return "#d1d5db"; // no data — gray
  if (pct >= 80) return "#008751";
  if (pct >= 60) return "#22c55e";
  if (pct >= 40) return "#f59e0b";
  if (pct >= 20) return "#f97316";
  return "#dc2626";
}

const LEGEND_ROWS = [
  { color: "#008751", label: "≥ 80% uploaded" },
  { color: "#22c55e", label: "60–79%" },
  { color: "#f59e0b", label: "40–59%" },
  { color: "#f97316", label: "20–39%" },
  { color: "#dc2626", label: "< 20% uploaded" },
  { color: "#d1d5db", label: "No data" },
];

// Normalise state names for fuzzy matching
function norm(s) {
  return (s ?? "").toLowerCase().replace(/[^a-z]/g, "");
}

// FlyTo helper — must be used inside MapContainer
function FlyTo({ coords, zoom }) {
  const map = useMap();
  useEffect(() => {
    if (coords) map.flyTo(coords, zoom, { animate: true, duration: 0.8 });
  }, [coords, zoom]);
  return null;
}

export default function MapPage() {
  const [geoJson, setGeoJson] = useState(null);
  const [selected, setSelected] = useState(null);    // { stateName, uploadPct, lgas }
  const [flyTarget, setFlyTarget] = useState(null);  // { coords, zoom }

  // Load GeoJSON from public/
  useEffect(() => {
    fetch("/nigeria-states.geojson")
      .then((r) => r.json())
      .then(setGeoJson)
      .catch(() => setGeoJson(null));
  }, []);

  // Elections
  const { data: electionsData } = useQuery({
    queryKey: ["elections"],
    queryFn: () => getElections({ page_size: 100 }),
  });

  // Upload stats by state
  const { data: uploadStats = [] } = useQuery({
    queryKey: ["upload-by-state"],
    queryFn: getUploadByState,
    refetchInterval: 60_000,
  });

  // Build lookup: normalisedStateName → stats row
  const statsMap = useMemo(() => {
    const m = {};
    uploadStats.forEach((s) => { m[norm(s.state_name)] = s; });
    return m;
  }, [uploadStats]);

  // GeoJSON style callback
  function styleFeature(feature) {
    const name = feature.properties.shapeName ?? feature.properties.NAME_1 ?? "";
    const stats = statsMap[norm(name)];
    return {
      fillColor: pctToColor(stats?.upload_pct),
      fillOpacity: 0.72,
      color: "#ffffff",
      weight: 1.5,
    };
  }

  // Click handler per feature
  function onEachFeature(feature, layer) {
    const name = feature.properties.shapeName ?? feature.properties.NAME_1 ?? "";
    const stats = statsMap[norm(name)];

    layer.on({
      click(e) {
        const center = e.latlng;
        setFlyTarget({ coords: [center.lat, center.lng], zoom: 8 });
        setSelected({ stateName: name, stats });
      },
      mouseover(e) {
        e.target.setStyle({ weight: 3, color: "#374151", fillOpacity: 0.88 });
        e.target.bringToFront();
      },
      mouseout(e) {
        e.target.setStyle(styleFeature(feature));
      },
    });

    const pct = stats ? `${stats.upload_pct}%` : "No data";
    layer.bindTooltip(
      `<strong>${name}</strong><br/>${pct} uploaded`,
      { sticky: true, opacity: 0.92 }
    );
  }

  return (
    <div>
      <div className="page-header">
        <h1>Nigeria Results Map</h1>
        <p>Choropleth shows EC8A image upload coverage by state. Click a state for detail.</p>
      </div>

      <div className="map-layout">
        {/* Map */}
        <div className="map-container">
          <MapContainer
            center={[9.082, 8.675]}
            zoom={6}
            style={{ height: "100%", width: "100%" }}
            scrollWheelZoom
          >
            <TileLayer
              url="https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png"
              attribution="© OpenStreetMap contributors"
              maxZoom={18}
            />

            {geoJson && (
              <GeoJSON
                key={JSON.stringify(statsMap)}
                data={geoJson}
                style={styleFeature}
                onEachFeature={onEachFeature}
              />
            )}

            {flyTarget && <FlyTo coords={flyTarget.coords} zoom={flyTarget.zoom} />}
          </MapContainer>

          {!geoJson && (
            <div style={{
              position: "absolute", inset: 0, display: "flex",
              alignItems: "center", justifyContent: "center",
              background: "rgba(255,255,255,0.75)", zIndex: 999,
              fontSize: "0.875rem", color: "#6b7280",
            }}>
              Loading state boundaries…
            </div>
          )}
        </div>

        {/* Sidebar */}
        <div className="map-legend">
          <div>
            <h3>Upload coverage</h3>
            <div className="legend-scale" style={{ marginTop: "0.5rem" }}>
              {LEGEND_ROWS.map((r) => (
                <div key={r.label} className="legend-row">
                  <div className="legend-swatch" style={{ background: r.color }} />
                  <span>{r.label}</span>
                </div>
              ))}
            </div>
          </div>

          {/* Summary totals */}
          {uploadStats.length > 0 && (
            <div>
              <h3>National summary</h3>
              <NationalSummary stats={uploadStats} />
            </div>
          )}

          {/* Selected state */}
          {selected && (
            <div className="map-info-panel">
              <h4>{selected.stateName}</h4>
              {selected.stats ? (
                <>
                  <div className="map-info-row"><span>Upload %</span><strong>{selected.stats.upload_pct}%</strong></div>
                  <div className="map-info-row"><span>Downloaded</span><span>{selected.stats.downloaded?.toLocaleString()}</span></div>
                  <div className="map-info-row"><span>Total PUs</span><span>{selected.stats.total?.toLocaleString()}</span></div>
                  <div className="map-info-row"><span>Missing URL</span><span>{selected.stats.missing?.toLocaleString()}</span></div>
                  <div className="map-info-row"><span>Broken URL</span><span>{selected.stats.broken?.toLocaleString()}</span></div>
                </>
              ) : (
                <p style={{ color: "#6b7280", fontSize: "0.8rem" }}>No upload data for this state yet.</p>
              )}
              <button
                className="btn btn-ghost btn-sm"
                style={{ marginTop: "0.75rem", width: "100%" }}
                onClick={() => { setSelected(null); setFlyTarget({ coords: [9.082, 8.675], zoom: 6 }); }}
              >
                Zoom out
              </button>
            </div>
          )}

          {!selected && uploadStats.length === 0 && (
            <p style={{ color: "#9ca3af", fontSize: "0.8rem" }}>
              No scrape data yet. Run the IReV scraper to populate this map.
            </p>
          )}
        </div>
      </div>
    </div>
  );
}

function NationalSummary({ stats }) {
  const total      = stats.reduce((s, r) => s + (r.total      ?? 0), 0);
  const downloaded = stats.reduce((s, r) => s + (r.downloaded ?? 0), 0);
  const missing    = stats.reduce((s, r) => s + (r.missing    ?? 0), 0);
  const pct        = total > 0 ? Math.round(downloaded / total * 100) : 0;

  return (
    <div style={{ marginTop: "0.4rem" }}>
      <div className="map-info-row"><span>National upload</span><strong>{pct}%</strong></div>
      <div className="map-info-row"><span>PUs with image</span><span>{downloaded.toLocaleString()}</span></div>
      <div className="map-info-row"><span>PUs visited</span><span>{total.toLocaleString()}</span></div>
      <div className="map-info-row"><span>Missing</span><span>{missing.toLocaleString()}</span></div>
    </div>
  );
}
