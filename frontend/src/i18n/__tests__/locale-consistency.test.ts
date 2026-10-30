// Adapted from KBase's locale-consistency test; IDP supports zh/en only.
import { describe, expect, it } from "vitest";
import en from "../locales/en.json";
import zh from "../locales/zh.json";

function flatten(obj: Record<string, unknown>, prefix = ""): Record<string, string> {
  return Object.fromEntries(Object.entries(obj).flatMap(([name, value]) => {
    const key = prefix ? `${prefix}.${name}` : name;
    return typeof value === "object" && value !== null
      ? Object.entries(flatten(value as Record<string, unknown>, key))
      : [[key, String(value)]];
  }));
}
export function placeholders(value: string): string[] {
  return [...new Set([...value.matchAll(/\{([a-zA-Z_][\w]*)\}/g)].map((m) => m[1]))].sort();
}

describe("locale consistency (zh is the source)", () => {
  const source = flatten(zh);
  const target = flatten(en);
  it("en has exactly the same keys as zh", () => {
    expect(Object.keys(target).sort()).toEqual(Object.keys(source).sort());
  });
  it("every translation preserves the placeholder set", () => {
    for (const [key, value] of Object.entries(source)) {
      expect(placeholders(target[key] ?? ""), key).toEqual(placeholders(value));
    }
  });
  it("has no empty English strings or untranslated Chinese", () => {
    for (const [key, value] of Object.entries(target)) {
      expect(value.trim(), key).not.toBe("");
      expect(value, key).not.toMatch(/[\u3400-\u9fff]/u);
    }
  });
});
