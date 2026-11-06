"""Shared List result views (brief A6); legacy Object cleaning stays stable."""


def output_shape_of(file, snapshot_pkg: dict | None = None) -> str:
    meta = file.document_meta or {}
    shape = meta.get("output_shape") or (meta.get("effective_schema") or {}).get("output_shape")
    if shape not in ("object", "list"):
        shape = (snapshot_pkg or {}).get("output_shape")
    return "list" if shape == "list" else "object"


def _plain(value):
    if isinstance(value, dict):
        if "$value" in value:
            return value["$value"]
        return {key: _plain(item) for key, item in value.items() if not key.startswith("$")}
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def clean(result, shape: str):
    """A6: List recurses through records/items; Object keeps its public contract."""
    if result is None:
        return None
    if shape == "list":
        rows = result.get("records") if isinstance(result, dict) else result
        return [_plain(row) for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []
    def legacy_value(value):
        if isinstance(value, dict):
            return value.get("$value")
        if isinstance(value, list):
            return [{key: item for key, item in row.items() if not key.startswith("$")}
                    if isinstance(row, dict) else row for row in value]
        return value
    return {key: legacy_value(value) for key, value in result.items()}


def review_fields(result, shape: str, threshold: int) -> list[str]:
    """A6: positional paths identify each pending List field independently."""
    names = []
    def pending(cell):
        return (isinstance(cell, dict) and cell.get("$confidence") is not None
                and (cell["$confidence"] < threshold or cell.get("$rule_failures")
                     or cell.get("$format_error")))
    if shape != "list":
        return [key for key, cell in (result or {}).items() if pending(cell)]
    for index, record in enumerate((result or {}).get("records") or []):
        if not isinstance(record, dict):
            continue
        for key, cell in record.items():
            path = f"records[{index}].{key}"
            if pending(cell):
                names.append(path)
            if isinstance(cell, list):
                for row_index, row in enumerate(cell):
                    for column, meta in (row.get("$cells", {}) if isinstance(row, dict) else {}).items():
                        if pending(meta):
                            names.append(f"{path}[{row_index}].{column}")
    return names
