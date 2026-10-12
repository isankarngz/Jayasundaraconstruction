import type { FinalClass, ImageItem } from "./types";

export const CLASS_LABEL: Record<FinalClass, string> = {
  MATCH_CONFIRMED: "Match",
  NO_MATCH_VERIFIED: "No match",
  REVIEW_REQUIRED: "Review required",
  PENDING: "Pending",
  DUPLICATE: "Duplicate",
};

export function ClassBadge({ c }: { c: FinalClass }) {
  return <span className={`badge b-${c}`}>{CLASS_LABEL[c]}</span>;
}

export function VerifBadge({ it }: { it: Pick<ImageItem, "verification" | "review_decision"> }) {
  if (it.verification === "human_verified") return <span className="badge b-human">✔ human-verified</span>;
  if (it.verification === "human_unresolved") return <span className="badge b-error">reviewer: unreadable</span>;
  if (it.verification === "pending") return <span className="badge b-PENDING">pending</span>;
  return <span className="badge b-auto">automated only</span>;
}

export function fmtBytes(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(0)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

export function fmtConf(c: number | null | undefined): string {
  return c === null || c === undefined ? "–" : c.toFixed(2);
}

export function fmtTime(ts: number | null | undefined): string {
  return ts ? new Date(ts * 1000).toLocaleString() : "";
}

export const FILTERS: { key: string; label: string }[] = [
  { key: "all", label: "All images" },
  { key: "unresolved", label: "Unresolved (audit)" },
  { key: "unreviewed", label: "Not yet human-reviewed" },
  { key: "match", label: "Matches" },
  { key: "no_match", label: "No match" },
  { key: "review", label: "Review required" },
  { key: "failed", label: "Processing errors" },
  { key: "pending", label: "Pending processing" },
  { key: "reviewed", label: "Human-reviewed" },
  { key: "duplicate", label: "Duplicates" },
];
