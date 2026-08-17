// API client. Auth-on: Bearer JWT from the session store, 401 kicks back to
// /login. Auth-off (lite/dev): the M1 dev headers keep working unchanged.
import { clearSession, session } from "./session";

async function req<T>(method: string, url: string, body?: unknown): Promise<T> {
  const headers: Record<string, string> = {
    "X-Tenant-Id": "default",
    "X-User": session.email || localStorage.getItem("idp_user") || "reviewer-1",
    ...(body ? { "Content-Type": "application/json" } : {}),
  };
  if (session.token) headers["Authorization"] = `Bearer ${session.token}`;
  const resp = await fetch(url, {
    method,
    headers,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (resp.status === 401 && session.authRequired) {
    clearSession();
    window.location.hash = "#/login";
    throw new Error("登录已过期，请重新登录");
  }
  if (!resp.ok) {
    let detail = "";
    try { detail = (await resp.json()).detail ?? ""; } catch { /* non-JSON body */ }
    throw new Error(detail || `请求失败（HTTP ${resp.status}）`);
  }
  return resp.json() as Promise<T>;
}

/** multipart variant (file uploads); browser sets the boundary header itself */
async function reqForm<T>(url: string, form: FormData): Promise<T> {
  const headers: Record<string, string> = {
    "X-Tenant-Id": "default",
    "X-User": session.email || localStorage.getItem("idp_user") || "reviewer-1",
  };
  if (session.token) headers["Authorization"] = `Bearer ${session.token}`;
  const resp = await fetch(url, { method: "POST", headers, body: form });
  if (resp.status === 401 && session.authRequired) {
    clearSession();
    window.location.hash = "#/login";
    throw new Error("登录已过期，请重新登录");
  }
  if (!resp.ok) {
    let detail = "";
    try { detail = (await resp.json()).detail ?? ""; } catch { /* non-JSON body */ }
    throw new Error(detail || `请求失败（HTTP ${resp.status}）`);
  }
  return resp.json() as Promise<T>;
}

/** multipart upload WITH byte progress. fetch() exposes no upload progress, so
 *  document submission goes through XHR: on a slow link a 40MB batch takes
 *  minutes, and a fake animation would be lying about where it actually is. */
function reqUpload<T>(url: string, form: FormData,
                      onProgress?: (sent: number, total: number) => void): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", url);
    xhr.setRequestHeader("X-Tenant-Id", "default");
    xhr.setRequestHeader(
      "X-User", session.email || localStorage.getItem("idp_user") || "reviewer-1");
    if (session.token) xhr.setRequestHeader("Authorization", `Bearer ${session.token}`);
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress?.(e.loaded, e.total);
    };
    xhr.onload = () => {
      if (xhr.status === 401 && session.authRequired) {
        clearSession();
        window.location.hash = "#/login";
        reject(new Error("登录已过期，请重新登录"));
        return;
      }
      if (xhr.status >= 200 && xhr.status < 300) {
        try { resolve(JSON.parse(xhr.responseText) as T); }
        catch { reject(new Error("服务端返回了无法解析的内容")); }
        return;
      }
      let detail = "";
      try { detail = JSON.parse(xhr.responseText).detail ?? ""; } catch { /* non-JSON */ }
      // the reverse proxy rejects oversized bodies with an HTML page, not JSON
      if (!detail && xhr.status === 413) detail = "本批文件超过服务器允许的请求大小";
      reject(new Error(detail || `上传失败（HTTP ${xhr.status}）`));
    };
    xhr.onerror = () => reject(new Error("网络中断，本批未提交"));
    xhr.send(form);
  });
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

export interface SkillStat {
  skill_code: string; total: number; decided: number;
  by_status: Record<string, number>;
  corrections: number; correction_rate: number | null;
  straight_through_rate: number | null;
  top_corrected_fields: { field: string; count: number }[];
}

export interface SkillInfo {
  skill_code: string; name: string; kind: string; state: string;
  /** highest published version, null = never published (cannot be submitted to) */
  published_version: number | null;
}

// —— upload surface ——
export interface UploadLimits {
  extensions: string[]; max_files: number; max_size_mb: number; max_batch_mb: number;
}
export interface SubmitResult {
  transaction_id: string;
  files: { file_id: string; original_filename: string }[];
}
export interface TxnStatus {
  transaction_id: string; status: string; skill_code: string; skill_version: number;
  files: { file_id: string; file_name: string; status: string;
           page_count: number; msg: string }[];
}

// —— Skill Studio types (mirror app/skillengine/schema.py) ——
export interface FieldSpec {
  name: string; type: string; instruction: string; mode: string; required: boolean;
  anchor_hints: string[]; enum_values: string[]; columns: FieldSpec[];
}
export interface ValidatorSpec {
  type: string; field?: string | null; pattern?: string | null;
  target?: string | null; parts: string[];
}
export interface SkillPackage {
  skill_code: string; name: string; description: string; kind: string;
  doc_type_hint: string;
  system_prompt: string; fields: FieldSpec[];
  few_shot: { input_excerpt: string; expected_output: Record<string, unknown> }[];
  validators: ValidatorSpec[];
  review_policy: { mode: string; confidence_threshold: number };
  model_binding: { extractor: string; fallback: string | null; challenger: string | null };
  parser: string | null; additional_rules: string;
}
export interface SkillDetail {
  skill_code: string; name: string; kind: string; state: string;
  versions: { version: number; status: string; changelog: string;
              created_at?: string }[];
  selected_version: number | null;
  latest_package: SkillPackage | null;
}
export interface HomeStats {
  remaining_credits: number; used_credits: number; today_usage: number;
  passed_pages: number; passed_docs: number; pending_verification: number;
  queued: number; processing: number; today_completed: number;
}
export interface FileRow {
  file_id: string; transaction_id: string; file_name: string; skill_code: string;
  type: string; size: number | null; page_count: number; status: string;
  created_at: string; updated_at: string | null; verified_by: string | null;
  error: string | null;
}
export interface FilesPage {
  total: number; page: number; page_size: number; total_pages: number; data: FileRow[];
}
export interface SmtpInfo {
  configured: boolean; has_password?: boolean; host?: string; port?: number;
  security?: string; username?: string; from_addr?: string; from_name?: string;
  reply_to?: string;
}
export interface DryRunEntry {
  provider: string; ok: boolean; result?: Record<string, FieldCell>;
  usage?: Record<string, number | string>; error?: string;
}

// —— /detect: seal/signature visual detection (design 2026-08-07) ——
export interface DetectRegion {
  page: number; label: string; score: number;
  x: number; y: number; w: number; h: number;   // percent (masking contract)
  bbox_px: number[];                            // page pixels (overlay contract)
  mask: number[][] | null;
}
export interface DetectResult {
  page_count: number;          // total pages in the document
  pages_scanned: number;       // rasterized+detected pages (≤100)
  truncated: boolean;          // true => pages past the cap were NOT examined
  detector: string;
  pages: { page: number; width: number; height: number }[];
  regions: DetectRegion[]; misses: string[];
}
/** DetectRegion + the raster page dims its bbox_px lives in, so DocStage can
 *  rescale into the UDR viewBox (detect rasters at 200 DPI, UDR pages may be
 *  PDF points — the two pixel spaces differ by a constant factor per page). */
export interface RegionOverlay extends DetectRegion {
  pageWidth: number; pageHeight: number;
}

export interface LoginResult {
  access_token: string; token_type: string; role: string;
  tenant_id: string; email: string; must_change_password: boolean;
}

// —— billing (§12) ——
export interface BillingAccount {
  tenant_id: string; plan: string | null;
  paid_balance: number; gift_balance: number; frozen: number; available: number;
  mode: string; byok: boolean;
  rates: Record<string, number>; rates_byok: Record<string, number>;
  gift_review_threshold: number; gift_monthly_cap: number;
}
export interface LedgerRow {
  id: string; kind: string; bucket: string; amount: number;
  transaction_id: string | null; note: string;
  balance_snapshot: number | null; created_at: string | null;
}
export interface GiftRequestRow {
  id: string; target_tenant_id: string; amount: number; campaign: string;
  reason: string; requested_by: string; status: string;
  decided_by: string | null; created_at: string | null;
}

/** authenticated binary fetch — <img>/pdf.js/anchor can't carry the Bearer */
export async function fetchBlob(url: string): Promise<Blob> {
  const headers: Record<string, string> = { "X-Tenant-Id": "default" };
  if (session.token) headers["Authorization"] = `Bearer ${session.token}`;
  const resp = await fetch(url, { headers });
  if (resp.status === 401 && session.authRequired) {
    clearSession();
    window.location.hash = "#/login";
    throw new Error("登录已过期，请重新登录");
  }
  if (!resp.ok) throw new Error(`下载失败（HTTP ${resp.status}）`);
  return resp.blob();
}

/** authenticated download-to-disk (export links) */
export async function downloadFile(url: string, filename: string): Promise<void> {
  const blob = await fetchBlob(url);
  const obj = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = obj;
  a.download = filename;
  a.click();
  URL.revokeObjectURL(obj);
}

export const api = {
  // upload surface: capability contract + submission + transaction polling
  formats: () => req<UploadLimits>("GET", "/api/v1/formats"),
  submitProcess: (files: File[], skillCode: string,
                  onProgress?: (sent: number, total: number) => void) => {
    const f = new FormData();
    for (const file of files) f.append("files", file, file.name);
    f.append("skill_code", skillCode);
    return reqUpload<SubmitResult>("/api/v1/process", f, onProgress);
  },
  txnStatus: (id: string) => req<TxnStatus>("GET", `/api/v1/status/${id}`),
  // review workbench
  queue: () => req<QueueItem[]>("GET", "/api/v1/review/queue"),
  detail: (id: string) => req<ReviewDetail>("GET", `/api/v1/review/${id}`),
  lock: (id: string) => req("POST", `/api/v1/review/${id}/lock`),
  unlock: (id: string) => req("POST", `/api/v1/review/${id}/unlock`),
  assign: (id: string, assignee: string) =>
    req("POST", `/api/v1/review/${id}/assign`, { assignee }),
  patchFields: (id: string,
                edits: { field: string; value: string; bbox?: number[]; page?: number }[]) =>
    req("PATCH", `/api/v1/review/${id}/fields`, { edits }),
  confirm: (id: string) => req("POST", `/api/v1/review/${id}/confirm`, { comment: "" }),
  reject: (id: string) => req("POST", `/api/v1/review/${id}/reject`, { comment: "" }),
  downloadUrl: (id: string) => `/api/v1/files/${id}/download`,
  // skill studio (batch D)
  skillDetail: (code: string, version?: number) =>
    req<SkillDetail>("GET",
      `/api/v1/skills/${code}${version ? `?version=${version}` : ""}`),
  skillDelete: (code: string) => req("DELETE", `/api/v1/skills/${code}`),
  skillCreate: (pkg: SkillPackage, changelog = "") =>
    req("POST", "/api/v1/skills", { package: pkg, changelog }),
  skillNewDraft: (code: string, pkg: SkillPackage, changelog = "") =>
    req<{ version: number }>("POST", `/api/v1/skills/${code}/versions`,
                             { package: pkg, changelog }),
  skillPublish: (code: string, version: number) =>
    req("POST", `/api/v1/skills/${code}/versions/${version}/publish`),
  skillExportUrl: (code: string, version?: number) =>
    `/api/v1/skills/${code}/export${version ? `?version=${version}` : ""}`,
  skillImport: (file: File) => {
    const f = new FormData();
    f.append("file", file);
    return reqForm<{ skill_code: string; version: number }>("/api/v1/skills/import", f);
  },
  skillProbe: (file: File, provider?: string) => {
    const f = new FormData();
    f.append("file", file);
    if (provider) f.append("provider", provider);
    return reqForm<{ doc_type: string; fields: FieldSpec[]; provider_used: string }>(
      "/api/v1/skills/probe", f);
  },
  skillDraftFromText: (text: string) =>
    req<{ doc_type: string; fields: FieldSpec[]; provider_used: string }>(
      "POST", "/api/v1/skills/draft-from-text", { text }),
  skillEnrich: (fields: FieldSpec[], doc_type = "") =>
    req<{ fields: { name: string; instruction: string;
                    columns?: { name: string; instruction: string }[] }[];
          provider_used: string }>(
      "POST", "/api/v1/skills/draft-enrich", { fields, doc_type }),
  skillDraftFromTable: (file: File) => {
    const f = new FormData();
    f.append("file", file);
    return reqForm<{ fields: FieldSpec[] }>("/api/v1/skills/draft-from-table", f);
  },
  skillDryRun: (file: File, pkg: SkillPackage, providers: string[]) => {
    const f = new FormData();
    f.append("file", file);
    f.append("package", JSON.stringify(pkg));
    f.append("providers", providers.join(","));
    return reqForm<{ runs: DryRunEntry[] }>("/api/v1/skills/dry-run", f);
  },
  goldenAdd: (code: string, file: File, expected: Record<string, string>) => {
    const f = new FormData();
    f.append("file", file);
    f.append("expected", JSON.stringify(expected));
    return reqForm(`/api/v1/skills/${code}/golden`, f);
  },
  goldenCheck: (code: string, version: number) =>
    req<{ samples: number; avg_match_rate?: number; note?: string;
          reports?: { ok: boolean; match_rate?: number; error?: string;
                      diffs?: Record<string, { expected: string; got: string }> }[] }>(
      "POST", `/api/v1/skills/${code}/versions/${version}/golden-check`),
  // data & stats
  skills: () => req<SkillInfo[]>("GET", "/api/v1/skills"),
  cabinet: (skill: string) =>
    req<{ rows: Record<string, string>[] }>("GET", `/api/v1/cabinet/${skill}`),
  cabinetCsvUrl: (skill: string) => `/api/v1/cabinet/${skill}/export.csv`,
  stats: () => req<{ skills: SkillStat[] }>("GET", "/api/v1/stats/skills"),
  homeStats: () => req<HomeStats>("GET", "/api/v1/stats/home"),
  usageStats: (days: number, skill_code?: string) =>
    req<{ by_day: { date: string; credits: number }[];
          by_skill: { skill_code: string; credits: number }[] }>(
      "GET", `/api/v1/stats/usage?days=${days}${skill_code ? `&skill_code=${skill_code}` : ""}`),
  files: (page: number, status?: string) =>
    req<FilesPage>("GET",
      `/api/v1/files?page=${page}&page_size=20${status ? `&status=${status}` : ""}`),
  // seal/signature detection (pure compute, no billing — /locate's visual twin)
  detect: (file: Blob, fileName: string, kinds = "seal,signature") => {
    const f = new FormData();
    f.append("file", file, fileName);
    f.append("kinds", kinds);
    return reqForm<DetectResult>("/api/v1/detect", f);
  },
  // account (§11.8)
  login: (email: string, password: string) =>
    req<LoginResult>("POST", "/api/v1/auth/login", { email, password }),
  forgot: (email: string) => req("POST", "/api/v1/auth/forgot", { email }),
  reset: (token: string, password: string) =>
    req("POST", "/api/v1/auth/reset", { token, password }),
  activate: (token: string, password: string) =>
    req("POST", "/api/v1/auth/activate", { token, password }),
  changePassword: (old_password: string, new_password: string) =>
    req("POST", "/api/v1/auth/change-password", { old_password, new_password }),
  // admin: users & platform settings (batch E)
  listUsers: () => req<{ id: string; email: string; role: string; active: boolean;
                         pending: boolean; auth_provider: string; created_at: string }[]>(
    "GET", "/api/v1/auth/users"),
  createUser: (email: string, password: string, role: string) =>
    req("POST", "/api/v1/auth/users", { email, password, role }),
  invite: (email: string, role: string) =>
    req("POST", "/api/v1/auth/invite", { email, role }),
  patchUser: (id: string, patch: { active?: boolean; role?: string }) =>
    req("PATCH", `/api/v1/auth/users/${id}`, patch),
  getSmtp: () => req<SmtpInfo>("GET", "/api/v1/settings/smtp"),
  putSmtp: (cfg: Record<string, unknown>) => req<SmtpInfo>("PUT", "/api/v1/settings/smtp", cfg),
  testSmtp: (to: string) => req("POST", "/api/v1/settings/smtp/test", { to }),
  listProviders: () => req<{ providers: { name: string; model: string; active: boolean;
                                          platform_key: boolean; byok_set: boolean }[] }>(
    "GET", "/api/v1/settings/providers"),
  listApiKeys: () => req<{ id: string; name: string; prefix: string; active: boolean;
                           quota_mode: string; allocated_balance: number;
                           allocated_frozen: number; created_at: string | null }[]>(
    "GET", "/api/v1/settings/api-keys"),
  keyQuotaMode: (id: string, mode: string) =>
    req("PUT", `/api/v1/settings/api-keys/${id}/quota`, { mode }),
  keyAllocate: (id: string, amount: number) =>
    req<{ allocated_balance: number; allocated_frozen: number }>(
      "POST", `/api/v1/settings/api-keys/${id}/allocate`, { amount }),
  // billing (§12): visibility + platform money ops
  billingAccount: () => req<BillingAccount>("GET", "/api/v1/billing/account"),
  billingLedger: (offset = 0, limit = 20, kind?: string) =>
    req<{ total: number; items: LedgerRow[] }>(
      "GET", `/api/v1/billing/ledger?offset=${offset}&limit=${limit}${kind ? `&kind=${kind}` : ""}`),
  billingConfig: (patch: Record<string, unknown>) =>
    req("PUT", "/api/v1/billing/config", patch),
  billingTopup: (amount: number, voucher_ref: string, note = "") =>
    req<{ available: number }>("POST", "/api/v1/billing/topup",
                               { amount, voucher_ref, note }),
  billingAdjust: (amount: number, bucket: string, reason: string) =>
    req<{ available: number }>("POST", "/api/v1/billing/adjust",
                               { amount, bucket, reason }),
  billingGift: (amount: number, campaign: string, reason: string) =>
    req<{ request_id: string; status: string }>(
      "POST", "/api/v1/billing/gift", { amount, campaign, reason }),
  billingGiftRequests: (status?: string) =>
    req<GiftRequestRow[]>(
      "GET", `/api/v1/billing/gift-requests${status ? `?status=${status}` : ""}`),
  billingGiftDecide: (id: string, decision: "approve" | "reject") =>
    req("POST", `/api/v1/billing/gift-requests/${id}/${decision}`),
  createApiKey: (name: string) =>
    req<{ id: string; name: string; prefix: string; api_key: string }>(
      "POST", "/api/v1/settings/api-keys", { name }),
  revokeApiKey: (id: string) => req("DELETE", `/api/v1/settings/api-keys/${id}`),
  oidcEnabled: () => req<{ enabled: boolean }>("GET", "/api/v1/auth/oidc/enabled"),
  getOidc: () => req<{ enabled: boolean; issuer?: string; client_id?: string;
                       has_secret?: boolean }>("GET", "/api/v1/settings/oidc"),
  putOidc: (cfg: { enabled: boolean; issuer: string; client_id: string;
                   client_secret?: string }) => req("PUT", "/api/v1/settings/oidc", cfg),
  putByok: (name: string, api_key: string) =>
    req("PUT", `/api/v1/settings/providers/${name}/key`, { api_key }),
  deleteByok: (name: string) => req("DELETE", `/api/v1/settings/providers/${name}/key`),
  get currentUser() { return session.email || localStorage.getItem("idp_user") || "reviewer-1"; },
};
