import { useEffect, useRef, useState } from "react";
import { api, type UploadResult } from "../api";
import { go } from "../App";
import type { Job } from "../types";
import { fmtBytes } from "../util";

interface Props { job: Job; refresh: () => void }
interface QItem { file: File; path: string }

const BATCH_FILES = 20;
const BATCH_BYTES = 48 * 1024 * 1024;

// Recursively collect files from a dropped folder.
async function walkEntry(entry: FileSystemEntry, prefix: string, out: QItem[]): Promise<void> {
  if (entry.isFile) {
    const file = await new Promise<File>((res, rej) => (entry as FileSystemFileEntry).file(res, rej));
    out.push({ file, path: prefix + file.name });
  } else if (entry.isDirectory) {
    const reader = (entry as FileSystemDirectoryEntry).createReader();
    for (;;) {
      const batch = await new Promise<FileSystemEntry[]>((res, rej) => reader.readEntries(res, rej));
      if (!batch.length) break;
      for (const e of batch) await walkEntry(e, `${prefix}${entry.name}/`, out);
    }
  }
}

export default function Upload({ job, refresh }: Props) {
  const [queue, setQueue] = useState<QItem[]>([]);
  const [running, setRunning] = useState(false);
  const [over, setOver] = useState(false);
  const [skipExisting, setSkipExisting] = useState(true);
  const [done, setDone] = useState({ files: 0, bytes: 0 });
  const [inflight, setInflight] = useState(0);
  const [log, setLog] = useState<UploadResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const stopRef = useRef(false);
  const totalBytes = queue.reduce((a, q) => a + q.file.size, 0) + done.bytes;

  useEffect(() => {
    const warn = (e: BeforeUnloadEvent) => { if (running) { e.preventDefault(); e.returnValue = ""; } };
    window.addEventListener("beforeunload", warn);
    return () => window.removeEventListener("beforeunload", warn);
  }, [running]);

  const add = (items: QItem[]) => setQueue((q) => [...q, ...items]);

  const onDrop = async (e: React.DragEvent) => {
    e.preventDefault();
    setOver(false);
    const items = Array.from(e.dataTransfer.items || []);
    const out: QItem[] = [];
    const entries = items.map((it) => it.webkitGetAsEntry?.()).filter(Boolean) as FileSystemEntry[];
    if (entries.length) {
      for (const en of entries) await walkEntry(en, "", out);
    } else {
      Array.from(e.dataTransfer.files).forEach((f) => out.push({ file: f, path: f.name }));
    }
    add(out);
  };

  const onPick = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    add(files.map((f) => ({ file: f, path: (f as File & { webkitRelativePath?: string }).webkitRelativePath || f.name })));
    e.target.value = "";
  };

  const start = async () => {
    setRunning(true);
    setError(null);
    stopRef.current = false;
    let pending = [...queue];
    try {
      if (skipExisting && pending.length) {
        const kept: QItem[] = [];
        const skipped: UploadResult[] = [];
        for (let i = 0; i < pending.length; i += 2000) {
          const chunk = pending.slice(i, i + 2000);
          const r = await api.uploadCheck(job.id, chunk.map((q) => ({ path: q.path, size: q.file.size })));
          chunk.forEach((q, k) => (r.present[k] ? skipped.push({ status: "duplicate", name: q.path, reason: "already in this collection (same path and size) – skipped" }) : kept.push(q)));
        }
        if (skipped.length) setLog((l) => [...skipped, ...l]);
        pending = kept;
        setQueue(kept);
      }
      while (pending.length && !stopRef.current) {
        const batch: QItem[] = [];
        let bytes = 0;
        while (pending.length && batch.length < BATCH_FILES && (bytes === 0 || bytes + pending[0].file.size <= BATCH_BYTES)) {
          const q = pending.shift()!;
          batch.push(q);
          bytes += q.file.size;
        }
        let attempt = 0;
        for (;;) {
          try {
            const res = await api.upload(job.id, batch, setInflight);
            setLog((l) => [...res.results.filter((r) => r.status !== "accepted"), ...l].slice(0, 5000));
            break;
          } catch (err) {
            attempt += 1;
            if (attempt >= 3) throw err;
            await new Promise((r) => setTimeout(r, 1500 * attempt));
          }
        }
        setInflight(0);
        setDone((d) => ({ files: d.files + batch.length, bytes: d.bytes + bytes }));
        setQueue([...pending]);
        refresh();
      }
    } catch (err) {
      setError(`${(err as Error).message}. The remaining ${pending.length} file(s) are still queued – press Start to resume.`);
    } finally {
      setRunning(false);
    }
  };

  const pct = totalBytes ? Math.round(((done.bytes + inflight) / totalBytes) * 100) : 0;
  const rejected = log.filter((l) => l.status === "rejected");
  const dups = log.filter((l) => l.status === "duplicate");

  return (
    <>
      <div
        className={`dropzone ${over ? "over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={onDrop}
      >
        <div style={{ fontSize: 18, fontWeight: 600 }}>Drop images or folders here</div>
        <p className="muted">JPG, JPEG, PNG, WEBP, BMP, TIFF. Other files are listed as rejected, never silently dropped.</p>
        <div className="row" style={{ justifyContent: "center" }}>
          <label className="primary" style={{ display: "inline-block" }}>
            <input type="file" multiple accept=".jpg,.jpeg,.png,.webp,.bmp,.tif,.tiff,image/*" onChange={onPick} style={{ display: "none" }} />
            <span className="badge" style={{ padding: "8px 14px", border: "1px solid var(--border)", cursor: "pointer" }}>Choose files…</span>
          </label>
          <label>
            <input type="file" multiple onChange={onPick} style={{ display: "none" }} {...{ webkitdirectory: "", directory: "" }} />
            <span className="badge" style={{ padding: "8px 14px", border: "1px solid var(--border)", cursor: "pointer" }}>Choose folder…</span>
          </label>
        </div>
      </div>

      <div className="panel" style={{ marginTop: 16 }}>
        <div className="row">
          <h2 style={{ margin: 0 }}>Upload queue</h2>
          <span className="spacer" />
          <label className="row small"><input type="checkbox" checked={skipExisting} onChange={(e) => setSkipExisting(e.target.checked)} />
            Skip files already in this collection (resume an interrupted upload)</label>
          {!running ? (
            <button className="primary" disabled={!queue.length} onClick={start}>Start upload ({queue.length})</button>
          ) : (
            <button onClick={() => (stopRef.current = true)}>Pause after current batch</button>
          )}
          <button disabled={running || !queue.length} onClick={() => setQueue([])}>Clear queue</button>
        </div>
        <div className="progress" style={{ marginTop: 12 }}>
          <div style={{ width: `${pct}%`, background: "var(--accent)" }} />
        </div>
        <div className="small muted" style={{ marginTop: 6 }}>
          {done.files} uploaded ({fmtBytes(done.bytes)}) · {queue.length} queued ({fmtBytes(queue.reduce((a, q) => a + q.file.size, 0))})
          · {dups.length} duplicates/skipped · {rejected.length} rejected
        </div>
        {running && <p className="small warnline">Keep this tab open while uploading. Files already uploaded are saved on the server and processing continues in the background.</p>}
        {error && <div className="banner bad" style={{ marginTop: 10 }}>{error}</div>}
        {!running && done.files > 0 && !queue.length && (
          <p>Upload finished. <a href={`#/job/${job.id}/dashboard`} onClick={() => go(job.id)}>Watch processing on the dashboard →</a></p>
        )}
      </div>

      {(rejected.length > 0 || dups.length > 0) && (
        <div className="panel">
          <h2>Upload notices</h2>
          <table className="data small">
            <thead><tr><th>Status</th><th>File</th><th>Detail</th></tr></thead>
            <tbody>
              {log.slice(0, 500).map((l, i) => (
                <tr key={i}>
                  <td><span className={`badge ${l.status === "rejected" ? "b-error" : "b-DUPLICATE"}`}>{l.status}</span></td>
                  <td className="mono">{l.name}</td>
                  <td>{l.reason ?? (l.duplicate_of ? `identical content to image #${l.duplicate_of}; kept as a duplicate entry` : "")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </>
  );
}
