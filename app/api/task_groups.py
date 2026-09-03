"""Display semantics for one uploaded file backed by internal child jobs.

The processing engine may fan a multi-document PDF into child FileRecords, but
the product surface is one task per uploaded root file.  Keep the aggregation
rules in one place so the task list, upload poller, queue and review workbench
cannot disagree about the root task's state.
"""
from collections import Counter
from datetime import datetime


def display_status(root, children: list) -> str:
    """User-facing status for one uploaded root file."""
    if not children:
        return root.status
    statuses = [c.status for c in children]
    if "processing" in statuses:
        return "processing"
    if "queued" in statuses:
        return "queued"
    if "pending_verification" in statuses:
        return "pending_verification"
    if "error" in statuses:
        return "error"
    if "rejected" in statuses:
        return "rejected"
    if statuses and all(s == "passed" for s in statuses):
        return "passed"
    return "completed"


def display_updated_at(root, children: list) -> datetime:
    values = [x.updated_at or x.created_at for x in [root, *children]]
    return max(values)


def display_verified_by(root, children: list) -> str | None:
    if not children:
        return root.verified_by
    reviewers = {c.verified_by for c in children if c.verified_by}
    decided = all(c.status in {"completed", "passed", "rejected"} for c in children)
    if not decided or not reviewers:
        return None
    if len(reviewers) == 1:
        return reviewers.pop()
    return f"{len(reviewers)} 人"


def display_error(root, children: list) -> str | None:
    if root.error:
        return root.error
    failed = sum(1 for c in children if c.status == "error" or c.error)
    return f"{failed} 个子任务失败" if failed else None


def display_processed_at(root, children: list):
    """When the pipeline finished with this root task. A split parent never
    extracts itself — its finish time is the last child's."""
    stamps = [x.processed_at for x in children if x.processed_at]
    if root.processed_at:
        stamps.append(root.processed_at)
    return max(stamps) if stamps else None


def summary(root, children: list) -> dict:
    counts = Counter(c.status for c in children)
    return {
        "status": display_status(root, children),
        "updated_at": display_updated_at(root, children),
        "processed_at": display_processed_at(root, children),
        "verified_by": display_verified_by(root, children),
        "error": display_error(root, children),
        "child_count": len(children),
        "pending_children": counts["pending_verification"],
        "status_counts": dict(counts),
    }
