import { flushPromises, mount } from "@vue/test-utils";
import { afterEach, expect, it, vi } from "vitest";
import { api, type UserRow } from "../api";
import TransferUserModal from "../components/TransferUserModal.vue";
import { i18n } from "../i18n";
import { clearSession, session } from "../session";
import SettingsView from "../views/SettingsView.vue";

const user: UserRow = {
  id: "member", email: "test@example.com", tenant_id: "default", role: "operator", active: true,
  pending: false, auth_provider: "local", created_at: "", can_transfer: true,
};
const dest = { id: "team-2", name: "Team Two" };
afterEach(() => {
  clearSession();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("offers transfers only for eligible users and refreshes the roster after transfer", async () => {
  session.role = "admin";
  session.authRequired = true;
  // Other settings tabs load their own configuration when mounted.
  vi.stubGlobal("fetch", vi.fn(async () => new Response("{}")));
  let moved = false;
  const ineligible = { ...user, id: "admin", email: "admin@example.com", can_transfer: false };
  vi.spyOn(api, "listUsers").mockImplementation(async () => moved ? [ineligible] : [user, ineligible]);
  vi.spyOn(api, "transferTenants").mockResolvedValue([dest]);
  const transfer = vi.spyOn(api, "transferUser").mockImplementation(async () => { moved = true; });
  const view = mount(SettingsView, { global: { plugins: [i18n] } });
  try {
    await view.findAll(".tabs button").find(b => b.text() === i18n.global.t("settings.users"))!.trigger("click");
    await flushPromises();
    const [member, admin] = view.findAll("tbody tr");
    const action = i18n.global.t("settings.transfer_user");
    expect(admin.text()).not.toContain(action);
    await member.findAll("button").find(b => b.text() === action)!.trigger("click");
    await flushPromises();
    const dialog = view.getComponent(TransferUserModal);
    const confirm = dialog.get(".modal-actions .primary");
    expect(confirm.attributes("disabled")).toBeDefined();
    expect(dialog.get("option[value='team-2']").text()).toContain(dest.name);
    await dialog.get("select").setValue(dest.id);
    await confirm.trigger("click");
    await flushPromises();
    expect(transfer).toHaveBeenCalledWith(user.id, dest.id);
    expect(view.findComponent(TransferUserModal).exists()).toBe(false);
    expect(view.text()).not.toContain(user.email);
    expect(view.text()).toContain(ineligible.email);
  } finally { view.unmount(); }
});

it("registers a destination separately and keeps rejected transfers open", async () => {
  vi.spyOn(api, "transferTenants").mockResolvedValue([]);
  const register = vi.spyOn(api, "registerTransferTenant").mockResolvedValue(dest);
  const transfer = vi.spyOn(api, "transferUser").mockRejectedValue(new Error("destination full"));
  const view = mount(TransferUserModal, { props: { user }, global: { plugins: [i18n] } });
  try {
    await flushPromises();
    expect(view.text()).toContain(i18n.global.t("settings.no_transfer_tenants"));
    await view.get(".register-link").trigger("click");
    const [idInput, nameInput] = view.findAll("input");
    await idInput.setValue(dest.id);
    await nameInput.setValue(dest.name);
    await view.get("form").trigger("submit");
    await flushPromises();
    expect(register).toHaveBeenCalledWith(dest.id, dest.name);
    expect(transfer).not.toHaveBeenCalled();
    expect(view.get("select").element.value).toBe(dest.id);
    await view.get(".modal-actions .primary").trigger("click");
    await flushPromises();
    expect(view.get("[role='alert']").text()).toBe("destination full");
    expect(view.emitted("moved")).toBeUndefined();
    expect(view.get(".modal-actions .primary").attributes("disabled")).toBeUndefined();
  } finally { view.unmount(); }
});
