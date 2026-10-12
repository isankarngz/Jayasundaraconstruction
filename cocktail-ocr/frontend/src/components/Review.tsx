import { useCallback, useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { go } from "../App";
import type { Hit, ImageDetail, Job } from "../types";
import { ClassBadge, FILTERS, VerifBadge, fmtBytes, fmtConf, fmtTime } from "../util";
import ImageViewer from "./ImageViewer";

interface Props { job: Job; refresh: () => void; imageId?: number; initialFilter?: string }

const KIND_LABEL: Record<string, string> = {
  exact: "exact word", spaced: "whitespace-normalised", confusion: "OCR-confusion candidate", near: "near-miss reading",
  compound: "merged / compound word", excluded: "look-alike (not a match)",
};

export default function Review({ job, refresh, imageId, initialFilter }: Props) {
  const [filter, setFilter] = useState(initialFilter || "unresolved");
  const [ids, setIds] = useState<number[]>([]);
  const [detail, setDetail] = useState<ImageDetail | null>(null);
  const [showBoxes, setShowBoxes] = useState(true);
  const [focus, setFocus] = useState<Hit | null>(null);
  const [zoom, setZoom] = useState<{ kind: "fit" | "full" | "in" | "out"; n: number }>({ kind: "fit", n: 0 });
  const [autoAdvance, setAutoAdvance] = useState(true);
  const [note, setNote] = useState("");
  const [toast, setToast] = useState<string | null>(null);
  const [showExcluded, setShowExcluded] = useState(true);

  useEffect(() => { setFilter(initialFilter || "unresolved"); }, [initialFilter]);
  // The navigation list is fetched once per filter so it stays stable while decisions change membership.
  useEffect(() => {
    api.imageIds(job.id, { filter, sort: "id" }).then((r) => {
      setIds(r.ids);
      if (!imageId && r.ids.length) go(job.id, "review", r.ids[0], filter);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [job.id, filter]);

  const load = useCallback(async (id: number) => {
    const d = await api.image(id);
    setDetail(d);
    setNote(d.review_note ?? "");
    setFocus(null);
  }, []);
  useEffect(() => { if (imageId) load(imageId).catch((e) => setToast(e.message)); }, [imageId, load]);

  const pos = imageId ? ids.indexOf(imageId) : -1;
  const nav = useCallback((delta: number) => {
    if (!ids.length) return;
    const i = pos < 0 ? 0 : Math.min(ids.length - 1, Math.max(0, pos + delta));
    go(job.id, "review", ids[i], filter);
  }, [ids, pos, job.id, filter]);

  const decide = useCallback(async (decision: "match" | "no_match" | "unreadable" | null) => {
    if (!detail) return;
    try {
      const d = await api.review(detail.id, decision, "full_view", note || undefined);
      setDetail(d);
      setToast(decision ? `#${detail.id} marked ${decision.replace("_", " ")}` : `#${detail.id} decision cleared`);
      refresh();
      if (decision && autoAdvance && pos >= 0 && pos < ids.length - 1) nav(1);
    } catch (e) {
      setToast((e as Error).message);
    }
  }, [detail, note, autoAdvance, pos, ids.length, nav, refresh]);

  const visibleHits = useMemo(() => (detail?.hits ?? []).filter((h) => showExcluded || h.kind !== "excluded"), [detail, showExcluded]);
  const occHits = useMemo(() => {
    // one representative (highest confidence) box per occurrence + every candidate
    const seen = new Set<string>();
    return visibleHits.filter((h) => {
      const key = h.occurrence ? `o${h.occurrence}` : `${h.kind}:${h.raw}:${Math.round(h.poly[0][0] / 10)}:${Math.round(h.poly[0][1] / 10)}`;
      if (seen.has(key)) return false;
      seen.add(key);
      return true;
    });
  }, [visibleHits]);

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement).tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      if (k === "m" || k === "1") decide("match");
      else if (k === "n" || k === "2") decide("no_match");
      else if (k === "u" || k === "3") decide("unreadable");
      else if (k === "x") decide(null);
      else if (k === "arrowright" || k === "j") nav(1);
      else if (k === "arrowleft" || k === "k") nav(-1);
      else if (k === "b") setShowBoxes((s) => !s);
      else if (k === "f") setZoom((z) => ({ kind: "fit", n: z.n + 1 }));
      else if (k === "z") setZoom((z) => ({ kind: "full", n: z.n + 1 }));
      else if (k === "+" || k === "=") setZoom((z) => ({ kind: "in", n: z.n + 1 }));
      else if (k === "-") setZoom((z) => ({ kind: "out", n: z.n + 1 }));
      else if (k === "h" && occHits.length) {
        const i = focus ? occHits.findIndex((h) => h.id === focus.id) : -1;
        setFocus(occHits[(i + 1) % occHits.length]);
      } else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [decide, nav, occHits, focus]);

  useEffect(() => { if (!toast) return; const t = setTimeout(() => setToast(null), 2200); return () => clearTimeout(t); }, [toast]);

  const primary = detail?.passes.find((p) => p.stage === "A");
  return (
    <>
      <div className="row" style={{ marginBottom: 10 }}>
        <select value={filter} onChange={(e) => { setFilter(e.target.value); go(job.id, "review", undefined, e.target.value); }}>
          {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
        <button onClick={() => nav(-1)} disabled={pos <= 0}>‹ Prev</button>
        <span className="small">{pos >= 0 ? pos + 1 : "–"} / {ids.length}</span>
        <button onClick={() => nav(1)} disabled={pos < 0 || pos >= ids.length - 1}>Next ›</button>
        <label className="row small"><input type="checkbox" checked={autoAdvance} onChange={(e) => setAutoAdvance(e.target.checked)} /> auto-advance</label>
        <span className="spacer" />
        <span className="small muted">
          <kbd>M</kbd> match <kbd>N</kbd> no match <kbd>U</kbd> unreadable <kbd>X</kbd> clear · <kbd>←</kbd>/<kbd>→</kbd> navigate · <kbd>H</kbd> next hit ·{" "}
          <kbd>F</kbd> fit <kbd>Z</kbd> 100% <kbd>+</kbd>/<kbd>-</kbd> zoom · <kbd>B</kbd> boxes
        </span>
      </div>
      {ids.length === 0 && <div className="banner ok">No images in this queue.</div>}
      {detail && (
        <div className="review-layout">
          <div className="viewer">
            <div className="toolbar">
              <button className="small" onClick={() => setZoom((z) => ({ kind: "fit", n: z.n + 1 }))}>Fit</button>
              <button className="small" onClick={() => setZoom((z) => ({ kind: "full", n: z.n + 1 }))}>100%</button>
              <button className="small" onClick={() => setZoom((z) => ({ kind: "in", n: z.n + 1 }))}>＋</button>
              <button className="small" onClick={() => setZoom((z) => ({ kind: "out", n: z.n + 1 }))}>－</button>
              <button className="small" onClick={() => setShowBoxes(!showBoxes)}>{showBoxes ? "Hide" : "Show"} boxes</button>
              <a className="small" style={{ color: "#9cf" }} href={api.view(detail.id)} target="_blank" rel="noreferrer">Open full size ↗</a>
              <span className="small" style={{ opacity: .75 }}>Ctrl+wheel to zoom · solid = word box, dashed = approximate</span>
            </div>
            <ImageViewer src={api.view(detail.id)} hits={visibleHits} showBoxes={showBoxes} focus={focus} zoomCmd={zoom} />
          </div>
          <div className="side">
            <div className="panel">
              <div style={{ fontWeight: 600, wordBreak: "break-all" }}>{detail.rel_path}</div>
              <div className="small muted">
                #{detail.id} · {detail.width}×{detail.height} {detail.format} · {fmtBytes(detail.size_bytes)} · <span className="mono">{detail.sha256.slice(0, 16)}…</span>
              </div>
              <div className="row" style={{ marginTop: 8 }}>
                <ClassBadge c={detail.final} /> <VerifBadge it={detail} />
                {detail.review_method && <span className="small muted">via {detail.review_method.replace("_", " ")} · {fmtTime(detail.reviewed_at)}</span>}
              </div>
              {detail.duplicate_of && <p className="small">Duplicate of <a href={`#/job/${job.id}/review/${detail.duplicate_of}`}>#{detail.duplicate_of}</a> – decisions apply to both.</p>}
              {detail.same_name.length > 0 && <p className="small warnline">Other file(s) with the same name but different content: {detail.same_name.map((i) => <a key={i} href={`#/job/${job.id}/review/${i}`}>#{i} </a>)}</p>}
              <div className="decisions" style={{ marginTop: 10 }}>
                <button className="good" onClick={() => decide("match")}>Match <kbd>M</kbd></button>
                <button className="bad" onClick={() => decide("no_match")}>No match <kbd>N</kbd></button>
                <button className="warn" onClick={() => decide("unreadable")}>Unreadable <kbd>U</kbd></button>
              </div>
              <textarea placeholder="Optional reviewer note" value={note} onChange={(e) => setNote(e.target.value)} rows={2} style={{ width: "100%", marginTop: 8 }} />
              <div className="row small" style={{ marginTop: 6 }}>
                <button className="small" onClick={() => decide(null)} disabled={!detail.review_decision}>Clear decision</button>
                <button className="small" onClick={async () => { await api.retryImage(detail.id); setToast("re-queued for OCR"); refresh(); load(detail.id); }}
                  disabled={detail.proc_status === "processing" || detail.proc_status === "duplicate"}>Re-run OCR</button>
                <a href={api.original(detail.id)}>Download original</a>
              </div>
            </div>
            <div className="panel">
              <h2>Automated result: {detail.auto_result ?? detail.proc_status}</h2>
              <ul className="small" style={{ paddingLeft: 18, margin: 0 }}>
                {detail.reasons.map((r, i) => <li key={i}>{r}</li>)}
              </ul>
              {detail.warnings.map((w, i) => <div key={i} className="small warnline">⚠ {w}</div>)}
              {detail.error && <div className="small errline">✖ {detail.error}</div>}
            </div>
            <div className="panel">
              <div className="row"><h2 style={{ margin: 0 }}>Detections ({occHits.length})</h2><span className="spacer" />
                <label className="small row"><input type="checkbox" checked={showExcluded} onChange={(e) => setShowExcluded(e.target.checked)} /> look-alikes</label></div>
              {occHits.length === 0 ? <p className="small muted">No target word or candidate was read by any OCR pass.</p> : (
                <ul className="hitlist" style={{ marginTop: 8 }}>
                  {occHits.map((h) => (
                    <li key={h.id} className={`k-${h.kind}`} onClick={() => setFocus(h)} style={focus?.id === h.id ? { borderColor: "var(--accent)" } : undefined}>
                      <div><b>{h.raw}</b> <span className="small muted">→ {h.target} · {KIND_LABEL[h.kind]}</span></div>
                      <div className="small">“{h.line_text}”</div>
                      <div className="small muted">conf {fmtConf(h.confidence)} · {h.engine} · {h.variant}{h.occurrence ? ` · occurrence ${h.occurrence}` : ""}</div>
                      <div className="small muted mono">box {h.poly.map((p) => `(${p[0].toFixed(0)},${p[1].toFixed(0)})`).join(" ")}{h.box_approx ? " ≈" : ""}</div>
                    </li>
                  ))}
                </ul>
              )}
            </div>
            <div className="panel">
              <h2>OCR text – original image ({primary?.engine ?? "n/a"})</h2>
              <pre className="ocr">{primary ? (primary.text || "(no text detected)") : "(not processed)"}</pre>
              <details style={{ marginTop: 8 }}>
                <summary className="small">All {detail.passes.length} OCR passes</summary>
                <table className="data small">
                  <thead><tr><th>#</th><th>Stage</th><th>Engine</th><th>Variant</th><th>Lines</th><th>Mean conf</th><th>ms</th></tr></thead>
                  <tbody>
                    {detail.passes.map((p) => (
                      <tr key={p.id} title={p.text.slice(0, 600)}>
                        <td>{p.seq}</td><td>{p.stage}</td><td>{p.engine}</td><td>{p.variant}</td>
                        <td>{p.status === "error" ? <span className="errline">error: {p.error}</span> : p.n_lines}</td>
                        <td>{fmtConf(p.mean_conf)}</td><td>{p.duration_ms}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </details>
            </div>
          </div>
        </div>
      )}
      {toast && <div className="toast">{toast}</div>}
    </>
  );
}
