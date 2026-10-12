import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import { go } from "../App";
import type { ImageItem, Job } from "../types";
import { CLASS_LABEL, FILTERS } from "../util";

interface Props { job: Job; refresh: () => void; initialFilter?: string }

export default function ContactSheet({ job, refresh, initialFilter }: Props) {
  const [filter, setFilter] = useState(initialFilter || "unreviewed");
  const [size, setSize] = useState(220);
  const [perPage, setPerPage] = useState(60);
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: ImageItem[] }>({ total: 0, items: [] });
  const [sel, setSel] = useState<Set<number>>(new Set());
  const [anchor, setAnchor] = useState<number | null>(null);
  const [msg, setMsg] = useState<string | null>(null);

  const load = useCallback(() => {
    api.images(job.id, { filter, offset: page * perPage, limit: perPage }).then(setData);
  }, [job.id, filter, page, perPage]);
  useEffect(() => { load(); }, [load]);
  useEffect(() => { setSel(new Set()); setPage(0); }, [filter, perPage]);

  const click = (e: React.MouseEvent, idx: number, id: number) => {
    const next = new Set(sel);
    if (e.shiftKey && anchor !== null) {
      const [a, b] = [Math.min(anchor, idx), Math.max(anchor, idx)];
      data.items.slice(a, b + 1).forEach((it) => next.add(it.id));
    } else if (next.has(id)) next.delete(id);
    else next.add(id);
    setAnchor(idx);
    setSel(next);
  };

  const mark = async (decision: string | null) => {
    if (!sel.size) return;
    if (decision === "no_match" && !confirm(
      `Mark ${sel.size} image(s) as "no match" based on the contact sheet?\n\nOnly do this if you have actually inspected each thumbnail ` +
      `and are confident no small or faint text is hidden. The method is recorded as "contact_sheet" in the audit trail.`)) return;
    const r = await api.reviewBulk(job.id, Array.from(sel), decision, "contact_sheet");
    setMsg(`${r.updated} image(s) updated`);
    setSel(new Set());
    load();
    refresh();
  };

  useEffect(() => {
    const on = (e: KeyboardEvent) => {
      if ((e.target as HTMLElement).tagName === "INPUT" || (e.target as HTMLElement).tagName === "SELECT") return;
      if (e.key === "a" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); setSel(new Set(data.items.map((i) => i.id))); }
      if (e.key === "Escape") setSel(new Set());
    };
    window.addEventListener("keydown", on);
    return () => window.removeEventListener("keydown", on);
  }, [data]);

  const pages = Math.max(1, Math.ceil(data.total / perPage));
  return (
    <>
      <div className="panel">
        <div className="row">
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
          </select>
          <label className="row small">Tile size <input type="range" min={120} max={520} value={size} onChange={(e) => setSize(Number(e.target.value))} /></label>
          <select value={perPage} onChange={(e) => setPerPage(Number(e.target.value))}>
            {[30, 60, 120, 240].map((n) => <option key={n} value={n}>{n} per page</option>)}
          </select>
          <span className="spacer" />
          <span className="small muted">{sel.size} selected · click, shift-click range, <kbd>Ctrl</kbd>+<kbd>A</kbd> all on page, double-click to open</span>
        </div>
        <div className="row" style={{ marginTop: 10 }}>
          <button className="good" disabled={!sel.size} onClick={() => mark("match")}>Mark selected: match</button>
          <button className="bad" disabled={!sel.size} onClick={() => mark("no_match")}>Mark selected: no match</button>
          <button className="warn" disabled={!sel.size} onClick={() => mark("unreadable")}>Mark selected: unreadable</button>
          <button disabled={!sel.size} onClick={() => mark(null)}>Clear decision</button>
          {msg && <span className="small muted">{msg}</span>}
        </div>
        <p className="small muted" style={{ marginBottom: 0 }}>
          Thumbnails are downscaled – fine print is not legible here. Use the contact sheet to triage quickly; open any image with small or
          dense text in the Review view at full resolution before deciding.
        </p>
      </div>
      <div className="sheet" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${size}px, 1fr))`, marginTop: 12 }}>
        {data.items.map((it, idx) => (
          <div key={it.id} className={`tile ${sel.has(it.id) ? "sel" : ""}`} onClick={(e) => click(e, idx, it.id)}
            onDoubleClick={() => go(job.id, "review", it.id, filter)} title={`${it.rel_path}\n${it.reasons.join("\n")}`}>
            {it.proc_status === "error" || it.proc_status === "queued" || it.proc_status === "processing"
              ? <div style={{ aspectRatio: "4/3", display: "grid", placeItems: "center" }} className="small muted">{it.proc_status === "error" ? "⚠ failed" : it.proc_status}</div>
              : <img src={api.thumb(it.id)} loading="lazy" alt="" />}
            <div className="cap">
              <span className={`badge b-${it.final}`}>{CLASS_LABEL[it.final]}</span>
              {it.review_decision && <span className="badge b-human">✔ {it.review_decision.replace("_", " ")}</span>}
              {it.top_hit && <b>{it.top_hit.raw}</b>}
            </div>
            <div className="cap" style={{ paddingTop: 0 }}><span className="name muted">{it.rel_path}</span></div>
          </div>
        ))}
      </div>
      <div className="pager">
        <button className="small" disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <span className="small">Page {page + 1} / {pages} · {data.total} images</span>
        <button className="small" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
      </div>
    </>
  );
}
