import { api } from "../api";
import type { Job } from "../types";

const ITEMS = [
  { kind: "all.csv", title: "Full classification CSV", desc: "Every uploaded image (including duplicates) with final classification, verification status, detected words, bounding boxes, raw OCR text, engines, confidence, reasons, warnings and errors." },
  { kind: "matches.csv", title: "Matches CSV", desc: "Only images whose final classification is MATCH." },
  { kind: "matches.zip", title: "Matching images (ZIP)", desc: "Original, unmodified files of every matching image. Original filenames are kept; collisions get a “ (2)” suffix. Includes manifest.csv." },
  { kind: "review.zip", title: "Review-required images (ZIP)", desc: "Original files of every image still classified REVIEW REQUIRED (uncertain, unreadable, failed, conflicting). Includes manifest.csv." },
  { kind: "report.html", title: "Verification report (HTML)", desc: "Processing statistics, audit status, unresolved images, failures, rejected uploads and the match list. Printable." },
  { kind: "report.json", title: "Verification report (JSON)", desc: "The same report as machine-readable JSON." },
];

export default function Exports({ job }: { job: Job }) {
  const s = job.summary;
  return (
    <>
      {!s.audit_complete && (
        <div className="banner warn">
          {s.audit_message} Exports reflect the current state and label every unresolved or automated-only row explicitly.
        </div>
      )}
      {s.strict_fully_verified && <div className="banner ok">Every image has a human decision. Exports reflect a fully human-verified collection.</div>}
      <div className="cards" style={{ marginTop: 16 }}>
        {ITEMS.map((it) => (
          <div key={it.kind} className="panel" style={{ margin: 0 }}>
            <h2>{it.title}</h2>
            <p className="small muted">{it.desc}</p>
            <a href={api.exportUrl(job.id, it.kind)} download><button className="primary">Download</button></a>
          </div>
        ))}
      </div>
      <p className="small muted" style={{ marginTop: 12 }}>
        Exports always include the complete dataset from the server, never just the rows visible on screen.
      </p>
    </>
  );
}
