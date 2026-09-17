// one status vocabulary for the whole app (chips + filters read this)
export const STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  processing: "处理中",
  pending_verification: "待校验",
  completed: "已完成",
  passed: "已通过",
  rejected: "已拒绝",
  split: "已分割",
  error: "失败",
};

/** 走查 #19: the Playground run vocabulary (server-derived, D2) */
export const RUN_STATUS_LABELS: Record<string, string> = {
  queued: "排队中",
  running: "运行中",
  completed: "完成",
  needs_review: "完成（待复核）",
  failed: "失败",
};

export const VERSION_LABELS: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  archived: "已归档",
};

/** D5 (#19): the no-login actor is stored as "anonymous" — the UI says 免登录 */
export function initiatorLabel(name: string | null | undefined): string {
  if (!name || name === "anonymous") return "免登录";
  return name;
}
