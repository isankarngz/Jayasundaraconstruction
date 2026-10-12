import { useEffect, useState } from "react";
import { api } from "../api";
import { go } from "../App";
import type { Health, Job, JobSettings } from "../types";

export default function JobList() {
  const [jobs, setJobs] = useState<Job[] | null>(null);
  const [health, setHealth] = useState<Health | null>(null);
  const [name, setName] = useState("");
  const [s, setS] = useState<JobSettings>({ fuzzy: true, thorough: false, audit_policy: "strict" });
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.jobs().then(setJobs).catch((e) => setErr(e.message));
    api.health().then(setHealth).catch((e) => setErr(e.message));
  }, []);

  const create = async () => {
    try {
      const j = await api.createJob(name.trim() || `Collection ${new Date().toLocaleDateString()}`, s);
      go(j.id, "upload");
    } catch (e) {
      setErr((e as Error).message);
    }
  };

  return (
    <>
      {err && <div className="banner bad">{err}</div>}
      <div className="panel">
        <h2>New collection</h2>
        <p className="muted">
          Searches every image for the visible words <b>cocktail</b> / <b>cocktails</b> (any capitalisation, font, size or
          orientation). Images are processed locally on this machine; nothing is sent to external services.
        </p>
        <div className="row">
          <input type="text" placeholder="Collection name" value={name} onChange={(e) => setName(e.target.value)}
            style={{ minWidth: 260 }} onKeyDown={(e) => e.key === "Enter" && create()} />
          <label className="row" title="Flag OCR-confusion candidates (cocktaiI, c0cktail), near misses and merged words for review">
            <input type="checkbox" checked={s.fuzzy} onChange={(e) => setS({ ...s, fuzzy: e.target.checked })} />
            Flag OCR-confusion candidates (recommended)
          </label>
          <label className="row" title="Run every OCR pass even after a confident match (slower; more complete occurrence counts)">
            <input type="checkbox" checked={s.thorough} onChange={(e) => setS({ ...s, thorough: e.target.checked })} />
            Thorough mode
          </label>
          <label className="row">
            Audit policy
            <select value={s.audit_policy} onChange={(e) => setS({ ...s, audit_policy: e.target.value as JobSettings["audit_policy"] })}>
              <option value="strict">Strict – every image needs a human decision</option>
              <option value="risk_based">Risk-based – human checks matches, reviews and failures</option>
            </select>
          </label>
          <button className="primary" onClick={create}>Create collection</button>
        </div>
      </div>
      <div className="panel">
        <h2>Collections</h2>
        {jobs === null ? <p className="muted">Loading…</p> : jobs.length === 0 ? <p className="muted">No collections yet.</p> : (
          <div className="cards">
            {jobs.map((j) => (
              <div key={j.id} className="metric jobcard" onClick={() => go(j.id)}>
                <div style={{ fontWeight: 600 }}>{j.name}</div>
                <div className="small muted">#{j.id} · {new Date(j.created_at * 1000).toLocaleString()} · {j.state}</div>
                <div className="small" style={{ marginTop: 6 }}>
                  {j.summary.total_unique} images · {j.summary.confirmed_matches} matches · {j.summary.review_required} review ·{" "}
                  {j.summary.reviewed_pct}% reviewed
                </div>
                <div className="small" style={{ marginTop: 4 }}>
                  {j.summary.strict_fully_verified ? <span className="badge b-human">fully human-verified</span>
                    : j.summary.pending > 0 ? <span className="badge b-PENDING">{j.summary.pending} pending</span>
                    : <span className="badge b-REVIEW_REQUIRED">{j.summary.unresolved} unresolved</span>}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
      {health && (
        <div className="panel small">
          <h2>OCR engines</h2>
          {Object.entries(health.engines).map(([k, e]) => (
            <div key={k}>
              <b>{k}</b>: {e.available ? <span style={{ color: "var(--match)" }}>{e.name} – {e.description}</span>
                : <span className="errline">unavailable – {e.error}</span>}
            </div>
          ))}
          <div className="muted">Workers: {health.workers} · Max file {health.limits.max_file_mb} MB · Max {health.limits.max_megapixels} MP · v{health.version}</div>
        </div>
      )}
    </>
  );
}
