import { useEffect, useState } from "react";
import { api } from "../api";
import { go } from "../App";
import type { Health, Job } from "../types";
import { fmtTime } from "../util";

interface Props { job: Job; refresh: () => void }

function Metric({ v, l, cls, onClick }: { v: number | string; l: string; cls?: string; onClick?: () => void }) {
  return (
    <div className={`metric ${cls ?? ""}`} onClick={onClick} style={onClick ? { cursor: "pointer" } : undefined}>
      <div className="v">{v}</div>
      <div className="l">{l}</div>
    </div>
  );
}

export default function Dashboard({ job, refresh }: Props) {
  const s = job.summary;
  const [health, setHealth] = useState<Health | null>(null);
  const [events, setEvents] = useState<Awaited<ReturnType<typeof api.events>>>([]);
  const [msg, setMsg] = useState<string | null>(null);

  useEffect(() => { api.health().then(setHealth).catch(() => undefined); }, []);
  useEffect(() => { api.events(job.id).then(setEvents).catch(() => undefined); }, [job]);

  const pct = (n: number) => (s.total_unique ? (100 * n) / s.total_unique : 0);
  const toggle = async () => { await api.patchJob(job.id, { state: job.state === "running" ? "paused" : "running" }); refresh(); };
  const retry = async (scope: "failed" | "review" | "all") => {
    if (scope === "all" && !confirm("Re-run OCR on every processed image? Human decisions are kept.")) return;
    const r = await api.retryJob(job.id, scope);
    setMsg(`${r.requeued} image(s) re-queued`);
    refresh();
  };
  const engineDown = health && Object.values(health.engines).some((e) => !e.available);

  return (
    <>
      <div className={`banner ${s.strict_fully_verified ? "ok" : s.audit_complete ? "warn" : s.pending ? "info" : "bad"}`}>
        {s.audit_message}
      </div>
      {engineDown && (
        <div className="banner bad" style={{ marginTop: 10 }}>
          An OCR engine is unavailable – affected images will be sent to review rather than marked negative.{" "}
          {Object.values(health!.engines).filter((e) => !e.available).map((e) => e.error).join("; ")}
        </div>
      )}
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="row">
          <h2 style={{ margin: 0 }}>Processing</h2>
          <span className="spacer" />
          <span className="muted">
            {s.processed} / {s.total_unique} processed{s.processing ? ` · ${s.processing} in progress` : ""}
            {job.state === "paused" ? " · PAUSED" : ""}
          </span>
          <button onClick={toggle} disabled={s.pending === 0 && job.state === "running"}>
            {job.state === "running" ? "⏸ Pause" : "▶ Resume"}
          </button>
          <button onClick={() => retry("failed")} disabled={!s.failed}>Retry failed ({s.failed})</button>
          <button onClick={() => retry("review")} title="Re-run OCR on images in review (e.g. after enabling an engine)">Re-run review items</button>
          <button onClick={() => retry("all")}>Re-run all</button>
        </div>
        <div className="progress" style={{ marginTop: 12 }}>
          <div style={{ width: `${pct(s.auto.match)}%`, background: "var(--match)" }} title="automated match" />
          <div style={{ width: `${pct(s.auto.review)}%`, background: "var(--review)" }} title="automated review" />
          <div style={{ width: `${pct(s.failed)}%`, background: "var(--fail)" }} title="failed" />
          <div style={{ width: `${pct(s.auto.no_match)}%`, background: "var(--neg)" }} title="automated no match" />
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>
          Automated outcomes: {s.auto.match} match · {s.auto.review} review · {s.failed} failed · {s.auto.no_match} no match · {s.pending} pending
        </div>
        {msg && <div className="small" style={{ marginTop: 6 }}>{msg}</div>}
      </div>

      <div className="grid-metrics" style={{ marginTop: 16 }}>
        <Metric v={s.total_unique} l="Unique images uploaded" />
        <Metric v={s.processed} l="Processed" cls="pending" />
        <Metric v={s.confirmed_matches} l="Matches" cls="match" onClick={() => go(job.id, "results", undefined, "match")} />
        <Metric v={s.verified_negatives} l="No match (verified)" cls="neg" onClick={() => go(job.id, "results", undefined, "no_match")} />
        <Metric v={s.review_required} l="Review required" cls="review" onClick={() => go(job.id, "review", undefined, "review")} />
        <Metric v={s.failed} l="Processing failures" cls="fail" onClick={() => go(job.id, "results", undefined, "failed")} />
        <Metric v={`${s.reviewed_pct}%`} l={`Human-reviewed (${s.human_reviewed}/${s.total_unique})`} />
        <Metric v={s.occurrences.total} l={`Occurrences: ${s.occurrences.cocktail}× cocktail, ${s.occurrences.cocktails}× cocktails`} />
        <Metric v={s.duplicates} l="Duplicate files (same hash)" onClick={() => go(job.id, "results", undefined, "duplicate")} />
        <Metric v={s.rejected_uploads} l="Rejected at upload" cls={s.rejected_uploads ? "fail" : ""} onClick={() => go(job.id, "audit")} />
        <Metric v={s.unresolved} l={`Unresolved (${s.policy} policy)`} cls={s.unresolved ? "review" : "match"} onClick={() => go(job.id, "review", undefined, "unresolved")} />
      </div>
      {s.occurrences.human_confirmed_matches_without_ocr_count > 0 && (
        <p className="small muted">
          {s.occurrences.human_confirmed_matches_without_ocr_count} human-confirmed match(es) had no confident OCR reading, so they add
          no occurrences to the count above.
        </p>
      )}

      <div className="panel" style={{ marginTop: 16 }}>
        <h2>Activity</h2>
        <table className="data small">
          <tbody>
            {events.slice(0, 25).map((e) => (
              <tr key={e.id}>
                <td className="muted" style={{ whiteSpace: "nowrap" }}>{fmtTime(e.at)}</td>
                <td>{e.kind}</td>
                <td>{e.image_id ? <a href={`#/job/${job.id}/review/${e.image_id}`}>#{e.image_id}</a> : ""}</td>
                <td className="mono" style={{ wordBreak: "break-all" }}>{(e.detail ?? "").slice(0, 200)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  );
}
