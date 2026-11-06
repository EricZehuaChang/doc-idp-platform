"""A4: identify possible mixed receipts from existing page-local source hits."""
from dataclasses import dataclass, field
from itertools import combinations
from math import isfinite

OVERLAP_IOU = 0.3
Box = tuple[float, float, float, float]


def _union(a: Box | None, b: Box) -> Box:
    return b if a is None else (min(a[0], b[0]), min(a[1], b[1]),
                                max(a[2], b[2]), max(a[3], b[3]))


def _area(box: Box) -> float:
    return max(0, box[2] - box[0]) * max(0, box[3] - box[1])


@dataclass
class _Region:
    bounds: Box
    headers: Box | None = None
    details: list[Box] = field(default_factory=list)


def _regions(record: dict) -> dict[int, _Region]:
    pages: dict[int, _Region] = {}

    def add(page, coords, *, detail: bool):
        if not isinstance(page, int) or page < 1 or not isinstance(coords, list) or len(coords) != 4:
            return
        if not all(isinstance(v, (int, float)) and isfinite(v) for v in coords):
            return
        box: Box = (coords[0], coords[1], coords[2], coords[3])
        if not _area(box):
            return
        region = pages.setdefault(page, _Region(box))
        region.bounds = _union(region.bounds, box)
        if detail:
            region.details.append(box)
        else:
            region.headers = _union(region.headers, box)

    for value in record.values():
        if isinstance(value, dict):
            add(value.get("$pages"), value.get("$bbox"), detail=False)
        elif isinstance(value, list):
            for row in value:
                if not isinstance(row, dict):
                    continue
                for cell in row.get("$cells", {}).values():
                    for hit in cell.get("$hits", []):
                        add(hit.get("page"), hit.get("bbox"), detail=True)
    return pages


def _inside_header(details: list[Box], headers: Box | None) -> bool:
    if headers is None:
        return False
    left, top, right, bottom = headers
    return any(left <= (x0 + x1) / 2 <= right and top <= (y0 + y1) / 2 <= bottom
               for x0, y0, x1, y1 in details)


def annotate_record_regions(records: list[dict]) -> bool:
    """Attach per-page outlines in original parser units; compare only on the same page."""
    regions = [_regions(record) for record in records]
    for record, pages in zip(records, regions):
        if pages:
            record["$record_bbox"] = [{"page": p, "bbox": list(pages[p].bounds)}
                                      for p in sorted(pages)]
    for first, second in combinations(regions, 2):
        for p in first.keys() & second.keys():
            a, b = first[p], second[p]
            x, y = a.bounds, b.bounds
            intersection = _area((max(x[0], y[0]), max(x[1], y[1]),
                                  min(x[2], y[2]), min(x[3], y[3])))
            if intersection > OVERLAP_IOU * (_area(x) + _area(y) - intersection):
                return True
            if _inside_header(a.details, b.headers) or _inside_header(b.details, a.headers):
                return True
    return False
