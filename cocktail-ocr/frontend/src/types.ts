export type FinalClass = "MATCH_CONFIRMED" | "NO_MATCH_VERIFIED" | "REVIEW_REQUIRED" | "PENDING" | "DUPLICATE";
export type Decision = "match" | "no_match" | "unreadable" | null;

export interface JobSettings {
  fuzzy: boolean;
  thorough: boolean;
  audit_policy: "strict" | "risk_based";
}

export interface Summary {
  policy: string;
  total_unique: number;
  duplicates: number;
  rejected_uploads: number;
  processed: number;
  processed_ok: number;
  pending: number;
  processing: number;
  failed: number;
  auto: { match: number; no_match: number; review: number };
  confirmed_matches: number;
  verified_negatives: number;
  review_required: number;
  human_reviewed: number;
  human_unreadable: number;
  reviewed_pct: number;
  occurrences: { cocktail: number; cocktails: number; total: number; human_confirmed_matches_without_ocr_count: number };
  unresolved: number;
  processing_complete: boolean;
  audit_complete: boolean;
  strict_fully_verified: boolean;
  audit_message: string;
}

export interface Job {
  id: number;
  name: string;
  created_at: number;
  state: "running" | "paused";
  settings: JobSettings;
  summary: Summary;
}

export interface TopHit {
  image_id: number;
  kind: string;
  raw: string;
  line_text: string;
  confidence: number | null;
  engine: string;
  variant: string;
}

export interface ImageItem {
  id: number;
  job_id: number;
  sha256: string;
  original_name: string;
  rel_path: string;
  format: string | null;
  size_bytes: number;
  width: number | null;
  height: number | null;
  proc_status: "queued" | "processing" | "done" | "error" | "duplicate";
  auto_result: "match" | "no_match" | "review" | null;
  error: string | null;
  attempts: number;
  best_confidence: number | null;
  occurrences: number;
  words_found: string;
  word_counts: Record<string, number>;
  review_decision: Decision;
  review_method: string | null;
  review_note: string | null;
  reviewed_at: number | null;
  duplicate_of: number | null;
  finished_at: number | null;
  sharpness: number | null;
  reasons: string[];
  warnings: string[];
  final: FinalClass;
  verification: "human_verified" | "human_unresolved" | "pending" | "automated_only";
  top_hit?: TopHit | null;
}

export interface Hit {
  id: number;
  kind: "exact" | "spaced" | "confusion" | "near" | "compound" | "excluded";
  target: string;
  raw: string;
  normalized: string;
  confidence: number | null;
  poly: number[][];
  box_approx: number;
  line_text: string;
  engine: string;
  variant: string;
  occurrence: number | null;
}

export interface OcrLineOut {
  text: string;
  conf: number | null;
  poly: number[][];
}

export interface Pass {
  id: number;
  seq: number;
  stage: string;
  engine: string;
  variant: string;
  duration_ms: number;
  status: string;
  error: string | null;
  n_lines: number;
  mean_conf: number | null;
  text: string;
  lines: OcrLineOut[] | null;
}

export interface ImageDetail extends ImageItem {
  hits: Hit[];
  passes: Pass[];
  duplicates: number[];
  same_name: number[];
  inherited?: ImageItem;
}

export interface Health {
  version: string;
  workers: number;
  engines: Record<string, { name: string; available: boolean; description?: string; error?: string }>;
  limits: { max_file_mb: number; max_megapixels: number };
  thresholds: Record<string, number>;
}
