import { useEffect, useState } from "react";
import { api } from "../api";
import { go } from "../App";
import type { ImageItem, Job } from "../types";
import { ClassBadge, VerifBadge } from "../util";

interface Props { job: Job; refresh: () => void }

function Check({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return <li><span className={`ic ${ok ? "ok" : "no"}`}>{ok ? "✔" : "✖"}</span><div>{children}</div></li>;
}

export default function Audit({ job, refresh }: Props) {
  const s = job.summary;
  const [unresolved, setUnresolved] = useState<{ total: number; items: ImageItem[] }>({ total: 0, items: [] });
  const [rejected, setRejected] = useState<Awaited<ReturnType<typeof api.rejected>>>([]);

  useEffect(() => {
    api.images(job.id, { filter: "unresolved", limit: 200 }).then(setUnresolved);
    api.rejected(job.id).then(setRejected);
  }, [job]);

  const setPolicy = async (p: "strict" | "risk_based") => { await api.patchJob(job.id, { audit_policy: p }); refresh(); };

  return (
    <>
      <div className={`banner ${s.strict_fully_verified ? "ok" : s.audit_complete ? "warn" : "bad"}`}>{s.audit_message}</div>
      <div className="panel" style={{ marginTop: 16 }}>
        <div className="row">
          <h2 style={{ margin: 0 }}>Completion audit</h2>
          <span className="spacer" />
          <label className="row small">Policy
            <select value={s.policy} onChange={(e) => setPolicy(e.target.value as "strict" | "risk_based")}>
              <option value="strict">Strict – every image needs a human decision</option>
              <option value="risk_based">Risk-based – matches, review items and failures need a human decision</option>
            </select>
          </label>
        </div>
        <p className="small muted">
          OCR confidence alone never qualifies an image as guaranteed correct. Under the strict policy the collection is reported as fully
          verified only when a person has recorded a decision for every unique image. The risk-based policy accepts automated negatives
          (which passed every OCR pass on two engines with no candidates) but the report then states clearly that the result is not 100%
          human-verified.
        </p>
        <ul className="checklist">
          <Check ok={s.total_unique > 0}>{s.total_unique} unique image(s) uploaded ({s.duplicates} duplicate files linked to their originals).</Check>
          <Check ok={s.rejected_uploads === 0}>
            {s.rejected_uploads === 0 ? "No uploads were rejected." : `${s.rejected_uploads} file(s) were rejected at upload and were not analysed – check the list below.`}
          </Check>
          <Check ok={s.pending === 0}>{s.pending === 0 ? "Every accepted image has been processed." : `${s.pending} image(s) still waiting for OCR.`}</Check>
          <Check ok={s.failed === 0 || unresolved.items.every((i) => i.proc_status !== "error")}>
            {s.failed} processing failure(s){s.failed ? " – each needs a human decision or a successful retry." : "."}
          </Check>
          <Check ok={s.review_required === 0}>{s.review_required} image(s) currently classified “review required” (including reviewer-marked unreadable: {s.human_unreadable}).</Check>
          <Check ok={s.policy !== "strict" || s.human_reviewed - s.human_unreadable >= s.total_unique}>
            Human decisions recorded for {s.human_reviewed - s.human_unreadable} of {s.total_unique} image(s) ({s.reviewed_pct}% including “unreadable”).
          </Check>
          <Check ok={s.unresolved === 0}>{s.unresolved} image(s) unresolved under the “{s.policy}” policy.</Check>
        </ul>
        <div className="row" style={{ marginTop: 12 }}>
          <button className="primary" disabled={!s.unresolved} onClick={() => go(job.id, "review", undefined, "unresolved")}>Review unresolved images →</button>
          <button disabled={!s.unresolved} onClick={() => go(job.id, "sheet", undefined, "unresolved")}>Open unresolved in contact sheet</button>
        </div>
      </div>

      <div className="panel">
        <h2>Unresolved images ({unresolved.total})</h2>
        {unresolved.total === 0 ? <p className="muted">None.</p> : (
          <table className="data small">
            <thead><tr><th>#</th><th>File</th><th>Classification</th><th>Verification</th><th>Why</th></tr></thead>
            <tbody>
              {unresolved.items.map((it) => (
                <tr key={it.id} className="clickable" onClick={() => go(job.id, "review", it.id, "unresolved")}>
                  <td>{it.id}</td><td style={{ wordBreak: "break-all" }}>{it.rel_path}</td>
                  <td><ClassBadge c={it.final} /></td><td><VerifBadge it={it} /></td>
                  <td>{it.error ?? it.reasons[0]}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {unresolved.total > unresolved.items.length && <p className="small muted">Showing first {unresolved.items.length}.</p>}
      </div>

      <div className="panel">
        <h2>Rejected uploads ({rejected.length})</h2>
        {rejected.length === 0 ? <p className="muted">None.</p> : (
          <table className="data small">
            <thead><tr><th>File</th><th>Reason</th></tr></thead>
            <tbody>{rejected.map((r) => <tr key={r.id}><td className="mono">{r.original_name}</td><td>{r.reason}</td></tr>)}</tbody>
          </table>
        )}
      </div>
    </>
  );
}
