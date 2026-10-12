import { useEffect, useState } from "react";
import { api } from "../api";
import { go } from "../App";
import type { ImageItem, Job } from "../types";
import { ClassBadge, FILTERS, VerifBadge, fmtBytes, fmtConf } from "../util";

interface Props { job: Job; initialFilter?: string }
const PAGE = 100;

export default function Results({ job, initialFilter }: Props) {
  const [filter, setFilter] = useState(initialFilter || "all");
  const [q, setQ] = useState("");
  const [sort, setSort] = useState("id");
  const [order, setOrder] = useState<"asc" | "desc">("asc");
  const [page, setPage] = useState(0);
  const [data, setData] = useState<{ total: number; items: ImageItem[] }>({ total: 0, items: [] });
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => { setFilter(initialFilter || "all"); }, [initialFilter]);
  useEffect(() => { setPage(0); }, [filter, q, sort, order]);
  useEffect(() => {
    const t = window.setTimeout(() => {
      api.images(job.id, { filter, q, sort, order, offset: page * PAGE, limit: PAGE })
        .then((d) => { setData(d); setErr(null); }).catch((e) => setErr(e.message));
    }, q ? 250 : 0);
    return () => window.clearTimeout(t);
  }, [job, filter, q, sort, order, page]);

  const th = (key: string, label: string) => (
    <th className="sortable" onClick={() => { if (sort === key) setOrder(order === "asc" ? "desc" : "asc"); else { setSort(key); setOrder("asc"); } }}>
      {label} {sort === key ? (order === "asc" ? "▲" : "▼") : ""}
    </th>
  );
  const pages = Math.max(1, Math.ceil(data.total / PAGE));

  return (
    <div className="panel">
      <div className="row" style={{ marginBottom: 12 }}>
        <select value={filter} onChange={(e) => setFilter(e.target.value)}>
          {FILTERS.map((f) => <option key={f.key} value={f.key}>{f.label}</option>)}
        </select>
        <input type="search" placeholder="Search filename, hash or OCR text near a hit…" value={q} onChange={(e) => setQ(e.target.value)} style={{ minWidth: 300 }} />
        <span className="spacer" />
        <span className="muted">{data.total} image(s)</span>
        <button onClick={() => go(job.id, "review", data.items[0]?.id, filter)} disabled={!data.items.length}>Review these →</button>
      </div>
      {err && <div className="banner bad">{err}</div>}
      <div style={{ overflowX: "auto" }}>
        <table className="data">
          <thead>
            <tr>
              <th></th>
              {th("filename", "File")}
              {th("classification", "Classification")}
              {th("verification", "Verification")}
              <th>Detected / reason</th>
              <th>Context (OCR line)</th>
              {th("confidence", "Conf.")}
              <th>Engine / method</th>
              {th("status", "Processing")}
            </tr>
          </thead>
          <tbody>
            {data.items.map((it) => (
              <tr key={it.id} className="clickable" onClick={() => go(job.id, "review", it.id, filter)}>
                <td>{it.proc_status === "error" || it.proc_status === "queued" ? <div className="thumb" /> : <img className="thumb" loading="lazy" src={api.thumb(it.id)} alt="" />}</td>
                <td style={{ maxWidth: 260, wordBreak: "break-all" }}>
                  <div>{it.rel_path}</div>
                  <div className="small muted">#{it.id} · {it.width && it.height ? `${it.width}×${it.height} · ` : ""}{fmtBytes(it.size_bytes)} · <span className="mono">{it.sha256.slice(0, 10)}</span></div>
                </td>
                <td><ClassBadge c={it.final} />{it.duplicate_of && <div className="small muted">dup of #{it.duplicate_of}</div>}</td>
                <td><VerifBadge it={it} /></td>
                <td style={{ maxWidth: 280 }}>
                  {it.top_hit ? <b>{it.top_hit.raw}</b> : null}
                  {it.words_found && <span className="small muted"> ({it.occurrences}× {it.words_found.replace(";", ", ")})</span>}
                  {!it.top_hit && !it.error && it.reasons[0] && <div className="small muted">{it.reasons[0]}</div>}
                  {it.error && <div className="small errline">{it.error}</div>}
                  {it.warnings.length > 0 && <div className="small warnline">⚠ {it.warnings[0]}</div>}
                </td>
                <td className="small" style={{ maxWidth: 240 }}>{it.top_hit?.line_text}</td>
                <td>{fmtConf(it.best_confidence ?? it.top_hit?.confidence)}</td>
                <td className="small">{it.top_hit ? `${it.top_hit.engine} · ${it.top_hit.variant}` : ""}</td>
                <td className="small">{it.proc_status}{it.attempts > 1 ? ` (${it.attempts} attempts)` : ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className="pager">
        <button className="small" disabled={page === 0} onClick={() => setPage(page - 1)}>‹ Prev</button>
        <span className="small">Page {page + 1} / {pages}</span>
        <button className="small" disabled={page + 1 >= pages} onClick={() => setPage(page + 1)}>Next ›</button>
      </div>
    </div>
  );
}
