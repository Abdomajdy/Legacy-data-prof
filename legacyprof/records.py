"""Work out how long a record is.

Three cases, in order of how often you hit them:

1. The file has line terminators and every line is the same length. Easy.
2. The file has no terminators at all -- a true fixed-block mainframe dataset.
   The record length has to be inferred.
3. The file is variable-length with 4-byte RDW headers (RECFM=VB). Detected
   and reported, not yet parsed.

For case 2 the trick is that at the correct record length, every byte column
lines up and column entropy collapses -- position 7 is always a digit, position
20 is always a space. At the wrong length, columns are a smear of everything
and entropy is near maximal. So: try every candidate length and take the one
that minimises mean column entropy.
"""

import math
from collections import Counter


def _column_entropy(data: bytes, reclen: int, max_records: int = 2000) -> float:
    n = min(len(data) // reclen, max_records)
    if n < 8:
        return math.inf
    cols = [Counter() for _ in range(reclen)]
    for r in range(n):
        row = data[r * reclen:(r + 1) * reclen]
        for i, b in enumerate(row):
            cols[i][b] += 1
    total = 0.0
    for c in cols:
        h = 0.0
        for count in c.values():
            p = count / n
            h -= p * math.log2(p)
        total += h
    return total / reclen


def detect_rdw(data: bytes) -> bool:
    """RECFM=VB records start with a 4-byte descriptor: 2-byte big-endian
    length including the RDW itself, then 2 zero bytes."""
    pos, checked = 0, 0
    while pos + 4 < len(data) and checked < 50:
        ln = int.from_bytes(data[pos:pos + 2], "big")
        if data[pos + 2:pos + 4] != b"\x00\x00" or ln < 5 or ln > 32767:
            return False
        pos += ln
        checked += 1
    return checked >= 8


def detect_reclen(data: bytes, min_len: int = 8, max_len: int = 4096) -> dict:
    """Return {'reclen', 'method', 'terminator', 'candidates'}."""
    # Case 1: line-terminated
    for term in (b"\r\n", b"\n"):
        if data.count(term) > 8:
            lines = data.split(term)
            lengths = Counter(len(x) for x in lines[:5000] if x)
            if lengths:
                most, count = lengths.most_common(1)[0]
                if count / sum(lengths.values()) > 0.98:
                    return {
                        "reclen": most,
                        "method": "line-terminated",
                        "terminator": repr(term),
                        "candidates": [],
                    }

    if detect_rdw(data):
        return {"reclen": None, "method": "rdw-variable", "terminator": None,
                "candidates": [], "note": "RECFM=VB detected; not yet supported"}

    # Case 2: entropy minimisation over lengths that divide the file evenly
    size = len(data)
    divisors = [L for L in range(min_len, min(max_len, size // 8) + 1) if size % L == 0]
    if not divisors:
        divisors = list(range(min_len, min(max_len, size // 8) + 1))

    scored = sorted(((_column_entropy(data, L), L) for L in divisors))
    if not scored or scored[0][0] is math.inf:
        return {"reclen": None, "method": "failed", "terminator": None, "candidates": []}

    best_h = scored[0][0]
    # Multiples of the true length also align, so prefer the smallest length
    # whose entropy is close to the minimum.
    near = sorted(L for h, L in scored if h <= best_h * 1.05)
    chosen = near[0]

    return {
        "reclen": chosen,
        "method": "entropy-minimisation",
        "terminator": None,
        "candidates": [{"reclen": L, "mean_col_entropy": round(h, 4)}
                       for h, L in scored[:5]],
    }


def iter_records(data: bytes, reclen: int, terminator: str | None = None):
    if terminator:
        term = eval(terminator)  # repr() of a bytes literal, produced above
        for line in data.split(term):
            if line:
                yield line
    else:
        for i in range(0, len(data) - reclen + 1, reclen):
            yield data[i:i + reclen]
