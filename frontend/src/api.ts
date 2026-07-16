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

export interface SkillInfo { skill_code: string; name: string; kind: string; state: string }

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
  skill_code: string; name: string; kind: string; doc_type_hint: string;
  system_prompt: string; fields: FieldSpec[];
  few_shot: { input_excerpt: string; expected_output: Record<string, unknown> }[];
  validators: ValidatorSpec[];
  review_policy: { mode: string; confidence_threshold: number };
  model_binding: { extractor: string; fallback: string | null; challenger: string | null };
  parser: string | null; additional_rules: string;
}
export interface SkillDetail {
  skill_code: string; name: string; kind: string; state: string;
  versions: { version: number; status: string; changelog: string }[];
  latest_package: SkillPackage | null;
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

export interface LoginResult {
  access_token: string; token_type: string; role: string;
  tenant_id: string; email: string; must_change_password: boolean;
}

export const api = {
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
  skillDetail: (code: string) => req<SkillDetail>("GET", `/api/v1/skills/${code}`),
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
  putByok: (name: string, api_key: string) =>
    req("PUT", `/api/v1/settings/providers/${name}/key`, { api_key }),
  deleteByok: (name: string) => req("DELETE", `/api/v1/settings/providers/${name}/key`),
  get currentUser() { return session.email || localStorage.getItem("idp_user") || "reviewer-1"; },
};
