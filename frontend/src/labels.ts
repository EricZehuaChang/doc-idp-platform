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

export const VERSION_LABELS: Record<string, string> = {
  draft: "草稿",
  published: "已发布",
  archived: "已归档",
};
