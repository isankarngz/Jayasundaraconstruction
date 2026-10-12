import type { Health, ImageDetail, ImageItem, Job, JobSettings } from "./types";

async function req<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* not json */
    }
    throw new Error(msg);
  }
  return res.json() as Promise<T>;
}

const json = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export interface ListParams {
  filter?: string;
  q?: string;
  sort?: string;
  order?: "asc" | "desc";
  offset?: number;
  limit?: number;
}

function qs(p: Record<string, unknown>): string {
  const u = new URLSearchParams();
  Object.entries(p).forEach(([k, v]) => {
    if (v !== undefined && v !== null && v !== "") u.set(k, String(v));
  });
  return u.toString();
}

export interface UploadResult {
  status: "accepted" | "duplicate" | "rejected";
  id?: number;
  name: string;
  reason?: string;
  duplicate_of?: number | null;
}

export const api = {
  health: () => req<Health>("/api/health"),
  jobs: () => req<Job[]>("/api/jobs"),
  job: (id: number) => req<Job>(`/api/jobs/${id}`),
  createJob: (name: string, s: JobSettings) => req<Job>("/api/jobs", json("POST", { name, ...s })),
  patchJob: (id: number, body: Partial<JobSettings> & { state?: string; name?: string }) =>
    req<Job>(`/api/jobs/${id}`, json("PATCH", body)),
  deleteJob: (id: number) => req(`/api/jobs/${id}`, { method: "DELETE" }),
  images: (id: number, p: ListParams) =>
    req<{ total: number; items: ImageItem[] }>(`/api/jobs/${id}/images?${qs({ ...p })}`),
  imageIds: (id: number, p: ListParams) =>
    req<{ total: number; ids: number[] }>(`/api/jobs/${id}/images?${qs({ ...p, ids_only: true })}`),
  image: (id: number) => req<ImageDetail>(`/api/images/${id}`),
  review: (id: number, decision: string | null, method = "full_view", note?: string) =>
    req<ImageDetail>(`/api/images/${id}/review`, json("POST", { decision, method, note })),
  reviewBulk: (job: number, ids: number[], decision: string | null, method = "contact_sheet") =>
    req<{ updated: number }>(`/api/jobs/${job}/review-bulk`, json("POST", { ids, decision, method })),
  retryImage: (id: number) => req<ImageDetail>(`/api/images/${id}/retry`, { method: "POST" }),
  retryJob: (id: number, scope: "failed" | "review" | "all") =>
    req<{ requeued: number }>(`/api/jobs/${id}/retry?scope=${scope}`, { method: "POST" }),
  rejected: (id: number) => req<{ id: number; original_name: string; reason: string; at: number }[]>(`/api/jobs/${id}/rejected`),
  events: (id: number) => req<{ id: number; at: number; kind: string; detail: string | null; image_id: number | null }[]>(`/api/jobs/${id}/events?limit=60`),
  uploadCheck: (id: number, files: { path: string; size: number }[]) =>
    req<{ present: boolean[] }>(`/api/jobs/${id}/upload/check`, json("POST", { files })),
  upload(id: number, files: { file: File; path: string }[], onProgress: (loaded: number) => void): Promise<{ results: UploadResult[] }> {
    return new Promise((resolve, reject) => {
      const fd = new FormData();
      files.forEach((f) => {
        fd.append("files", f.file, f.file.name);
        fd.append("paths", f.path);
      });
      const xhr = new XMLHttpRequest();
      xhr.open("POST", `/api/jobs/${id}/upload`);
      xhr.upload.onprogress = (e) => onProgress(e.loaded);
      xhr.onload = () => {
        if (xhr.status >= 200 && xhr.status < 300) resolve(JSON.parse(xhr.responseText));
        else reject(new Error(`upload failed: ${xhr.status} ${xhr.responseText.slice(0, 200)}`));
      };
      xhr.onerror = () => reject(new Error("network error during upload"));
      xhr.send(fd);
    });
  },
  thumb: (id: number) => `/api/images/${id}/thumb`,
  view: (id: number) => `/api/images/${id}/view`,
  original: (id: number) => `/api/images/${id}/original`,
  exportUrl: (id: number, kind: string) => `/api/jobs/${id}/export/${kind}`,
};
