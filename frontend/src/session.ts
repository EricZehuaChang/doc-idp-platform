// Session store: JWT + identity, persisted in localStorage. authRequired is
// probed once at boot — "off" deployments (lite/dev) keep the M1 no-login UX,
// "on" deployments get the login flow. Kept framework-minimal (reactive object).
import { reactive } from "vue";

export const session = reactive({
  token: localStorage.getItem("idp_token") || "",
  email: localStorage.getItem("idp_email") || "",
  role: localStorage.getItem("idp_role") || "",
  tenant: "",
  // null = not probed yet; false = auth off (no login needed); true = login required
  authRequired: null as boolean | null,
  mustChangePassword: false,
});

export function setSession(p: { access_token: string; email: string; role: string;
                                tenant_id: string; must_change_password?: boolean }) {
  session.token = p.access_token;
  session.email = p.email;
  session.role = p.role;
  session.tenant = p.tenant_id;
  session.mustChangePassword = !!p.must_change_password;
  localStorage.setItem("idp_token", p.access_token);
  localStorage.setItem("idp_email", p.email);
  localStorage.setItem("idp_role", p.role);
}

export function clearSession() {
  session.token = "";
  session.email = "";
  session.role = "";
  session.tenant = "";
  session.mustChangePassword = false;
  localStorage.removeItem("idp_token");
  localStorage.removeItem("idp_email");
  localStorage.removeItem("idp_role");
}

/** Boot probe: /auth/me with whatever credential we have. 200 → session valid
 * (or auth is off); 401 → login required (and any stored token is stale). */
export async function probeAuth(): Promise<void> {
  const headers: Record<string, string> = { "X-Tenant-Id": "default" };
  if (session.token) headers["Authorization"] = `Bearer ${session.token}`;
  try {
    const r = await fetch("/api/v1/auth/me", { headers });
    if (r.ok) {
      const me = await r.json();
      session.authRequired = !!session.token;    // off-mode /me also answers 200
      if (session.token) {
        session.email = me.email;
        session.role = me.role;
        session.tenant = me.tenant_id;
      }
    } else {
      session.authRequired = true;
      if (session.token) clearSession();          // stale/expired token
    }
  } catch {
    session.authRequired = null;                  // backend unreachable: retry later
  }
}
