// Thin API client. Dev identity headers (X-Tenant-Id / X-User) — replaced by
// real auth in M1.5; the header names match backend tenancy/review contracts.
const TENANT = "default";
const USER = localStorage.getItem("idp_user") || "reviewer-1";

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const resp = await fetch(url, {
    method,
    headers: {
      "X-Tenant-Id": TENANT,
      "X-User": USER,
      ...(body ? { "Content-Type": "application/json" } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`${resp.status}: ${text.slice(0, 200)}`);
  }
  return resp.json() as Promise<T>;
}

export interface QueueItem {
  file_id: string; file_name: string; skill_code: string;
  transaction_id: string; page_count: number;
  assignee: string | null; locked_by: string | null; created_at: string;
}

export interface FieldCell {
  $value: string; $confidence: number;
  $bbox: number[]; $pages: number | string;
  inferred?: boolean; $reasoning?: string; $corrected?: boolean;
  $rule_failures?: string[];
}

export interface ReviewDetail {
  file_id: string; file_name: string; status: string;
  result: Record<string, FieldCell | Record<string, string>[]>;
  pages: { page_no: number; width: number; height: number }[];
  locked_by: string | null; verified_by: string | null;
}

export const api = {
  queue: () => req<QueueItem[]>("GET", "/api/v1/review/queue"),
  detail: (id: string) => req<ReviewDetail>("GET", `/api/v1/review/${id}`),
  lock: (id: string) => req("POST", `/api/v1/review/${id}/lock`),
  unlock: (id: string) => req("POST", `/api/v1/review/${id}/unlock`),
  patchFields: (id: string, edits: { field: string; value: string }[]) =>
    req("PATCH", `/api/v1/review/${id}/fields`, { edits }),
  confirm: (id: string) => req("POST", `/api/v1/review/${id}/confirm`, { comment: "" }),
  reject: (id: string) => req("POST", `/api/v1/review/${id}/reject`, { comment: "" }),
  downloadUrl: (id: string) => `/api/v1/files/${id}/download`,
  currentUser: USER,
};
