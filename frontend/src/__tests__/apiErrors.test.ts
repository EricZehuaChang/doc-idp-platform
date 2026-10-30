import { describe, expect, it } from "vitest";
import { apiErrorMessage } from "../apiErrors";
import { setLanguage, translateMessage } from "../i18n";

describe("API error localization", () => {
  it("maps structured errors by code and resolves them at display time", () => {
    const message = apiErrorMessage({ code: "original_missing", message: "server detail" });
    expect(message).toEqual({ key: "api.original_missing" });
    setLanguage("en");
    expect(translateMessage(message)).toContain("original file");
    setLanguage("zh");
    expect(translateMessage(message)).not.toContain("original file");
  });
  it("keeps legacy details and handles unknown structured codes", () => {
    expect(apiErrorMessage("legacy detail")).toBe("legacy detail");
    expect(apiErrorMessage({ code: "future_code", message: "internal detail" })).toEqual({ key: "api.unknown-error" });
    expect(apiErrorMessage(null)).toBe("");
  });
});
