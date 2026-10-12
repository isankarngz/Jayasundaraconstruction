import { useCallback, useEffect, useState } from "react";
import { api } from "./api";
import type { Job } from "./types";
import JobList from "./components/JobList";
import Dashboard from "./components/Dashboard";
import Upload from "./components/Upload";
import Results from "./components/Results";
import ContactSheet from "./components/ContactSheet";
import Review from "./components/Review";
import Audit from "./components/Audit";
import Exports from "./components/Exports";

export type Tab = "dashboard" | "upload" | "results" | "sheet" | "review" | "audit" | "export";
const TABS: { key: Tab; label: string }[] = [
  { key: "dashboard", label: "Dashboard" },
  { key: "upload", label: "Upload" },
  { key: "results", label: "Results" },
  { key: "sheet", label: "Contact sheet" },
  { key: "review", label: "Review" },
  { key: "audit", label: "Audit" },
  { key: "export", label: "Export" },
];

interface Route {
  job?: number;
  tab: Tab;
  image?: number;
  filter?: string;
}

function parse(): Route {
  const h = window.location.hash.replace(/^#\/?/, "");
  const [path, query] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  const params = new URLSearchParams(query || "");
  if (parts[0] === "job" && parts[1]) {
    return {
      job: Number(parts[1]),
      tab: (parts[2] as Tab) || "dashboard",
      image: parts[3] ? Number(parts[3]) : undefined,
      filter: params.get("filter") || undefined,
    };
  }
  return { tab: "dashboard" };
}

export function go(job: number | undefined, tab: Tab = "dashboard", image?: number, filter?: string) {
  if (job === undefined) {
    window.location.hash = "#/";
    return;
  }
  let h = `#/job/${job}/${tab}`;
  if (image) h += `/${image}`;
  if (filter) h += `?filter=${encodeURIComponent(filter)}`;
  window.location.hash = h;
}

export default function App() {
  const [route, setRoute] = useState<Route>(parse());
  const [job, setJob] = useState<Job | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    const on = () => setRoute(parse());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const refresh = useCallback(async () => {
    if (!route.job) return;
    try {
      setJob(await api.job(route.job));
      setErr(null);
    } catch (e) {
      setErr(String((e as Error).message));
    }
  }, [route.job]);

  useEffect(() => {
    setJob(null);
    refresh();
  }, [route.job, refresh]);

  // Poll while work is outstanding (and slowly otherwise, to pick up other tabs' changes).
  useEffect(() => {
    if (!route.job) return;
    const busy = job && job.summary.pending > 0 && job.state === "running";
    const t = window.setInterval(refresh, busy ? 2000 : 8000);
    return () => window.clearInterval(t);
  }, [route.job, job, refresh]);

  return (
    <div className="app">
      <header className="top">
        <h1 style={{ cursor: "pointer" }} onClick={() => go(undefined)}>🍸 Cocktail Finder</h1>
        {job && <span className="crumb">/ {job.name}</span>}
        {job && (
          <nav className="tabs">
            {TABS.map((t) => (
              <button key={t.key} className={route.tab === t.key ? "active" : ""} onClick={() => go(job.id, t.key)}>
                {t.label}
                {t.key === "review" && job.summary.unresolved > 0 ? ` (${job.summary.unresolved})` : ""}
              </button>
            ))}
          </nav>
        )}
        <span className="spacer" />
        {job && job.summary.processing_complete && !job.summary.audit_complete && (
          <span className="badge b-REVIEW_REQUIRED" title={job.summary.audit_message}>
            ⚠ {job.summary.unresolved} unresolved
          </span>
        )}
      </header>
      <main>
        {err && <div className="banner bad">{err}</div>}
        {!route.job && <JobList />}
        {route.job && job && (
          <>
            {route.tab === "dashboard" && <Dashboard job={job} refresh={refresh} />}
            {route.tab === "upload" && <Upload job={job} refresh={refresh} />}
            {route.tab === "results" && <Results job={job} initialFilter={route.filter} />}
            {route.tab === "sheet" && <ContactSheet job={job} refresh={refresh} initialFilter={route.filter} />}
            {route.tab === "review" && (
              <Review job={job} refresh={refresh} imageId={route.image} initialFilter={route.filter} />
            )}
            {route.tab === "audit" && <Audit job={job} refresh={refresh} />}
            {route.tab === "export" && <Exports job={job} />}
          </>
        )}
      </main>
    </div>
  );
}
