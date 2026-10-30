import type { MessageDescriptor } from "./i18n";

const ERROR_CODES = new Set([
  "role_required",
  "fast_mode_advanced_conflict",
  "fast_mode_requires_standard_fields",
  "reference_unavailable",
  "sample_not_found",
  "sample_too_large",
  "sample_type_rejected",
  "skill_code_required",
  "no_input",
  "no_fields_generated",
  "validation_failed",
  "skill_not_found",
  "version_not_found",
  "run_not_found",
  "sample_parse_failed",
  "version_referenced",
  "import_token_expired",
  "package_decrypt_failed",
  "package_invalid",
  "reference_target_invalid",
  "original_missing",
  "skill_not_allowed_for_key",
  "idempotency_conflict",
  "fast_mode_page_limit",
  "artifact_not_ready"
]);

/** Resolve protocol errors without tying a pure function to the current language. */
export function apiErrorMessage(detail: unknown): MessageDescriptor | string {
  if (typeof detail === "string") return detail;
  if (!detail || typeof detail !== "object") return "";
  const value = detail as { code?: unknown; message?: unknown };
  if (typeof value.code === "string") {
    return { key: ERROR_CODES.has(value.code) ? `api.${value.code}` : "api.unknown-error" };
  }
  return typeof value.message === "string" ? value.message : "";
}
