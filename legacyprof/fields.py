"""Infer field boundaries, then classify each field.

Boundaries come from two sources, in this order:

1. Packed decimal runs, found first because a COMP-3 field is binary and its
   character-class profile is meaningless. Missing these first corrupts every
   boundary downstream of them.
2. Character-class transitions in the remaining columns. Position 19 is always
   alpha-or-space and position 20 is always a digit, so there is a boundary
   between them.

Classification then runs constraint checks against the real bytes and reports
conformance rates, never booleans.
"""

import re
from collections import Counter
from dataclasses import dataclass, field as dc_field

from . import codecs
from .columns import (CLASS_ALPHA, CLASS_DIGIT, CLASS_SPACE, ColumnStats,
                      class_map)


@dataclass
class Field:
    start: int
    end: int            # exclusive
    kind: str = "unknown"
    detail: str = ""
    conformance: float = 0.0
    notes: list = dc_field(default_factory=list)
    nulls: dict = dc_field(default_factory=dict)
    samples: list = dc_field(default_factory=list)
    cardinality: int = 0
    candidate_splits: list = dc_field(default_factory=list)

    @property
    def length(self) -> int:
        return self.end - self.start

    @property
    def label(self) -> str:
        return f"{self.start + 1}-{self.end}"


def _nibble_rates(cols: list[ColumnStats]) -> tuple[list[float], list[float]]:
    body, sign = [], []
    for c in cols:
        if not c.n:
            body.append(0.0)
            sign.append(0.0)
            continue
        b = sum(cnt for byte, cnt in c.hist.items()
                if (byte >> 4) <= 9 and (byte & 0x0F) <= 9)
        s = sum(cnt for byte, cnt in c.hist.items()
                if (byte >> 4) <= 9 and (byte & 0x0F) >= 0xA)
        body.append(b / c.n)
        sign.append(s / c.n)
    return body, sign


def find_packed_runs(cols: list[ColumnStats], records: list[bytes],
                     threshold: float = 0.98) -> list[tuple[int, int]]:
    """A COMP-3 field is a run of digit-pair bytes ending in a sign nibble."""
    body, sign = _nibble_rates(cols)
    runs, i = [], 0
    n = len(cols)
    while i < n:
        if sign[i] >= threshold:
            start = i
            while start > 0 and body[start - 1] >= threshold:
                start -= 1
            if i - start >= 1:  # at least 2 bytes total
                sample = [r[start:i + 1] for r in records[:2000]]
                rate = codecs.conformance(sample, codecs.is_packed)
                if rate >= threshold:
                    runs.append((start, i + 1))
                    i += 1
                    continue
        i += 1
    # drop overlaps, keep the longest
    runs.sort(key=lambda r: (r[0], -(r[1] - r[0])))
    kept = []
    for r in runs:
        if not kept or r[0] >= kept[-1][1]:
            kept.append(r)
    return kept


def find_zoned_runs(cols: list[ColumnStats], records: list[bytes], encoding: str,
                    threshold: float = 0.98) -> list[tuple[int, int]]:
    """A zoned field is a run of digit columns ending in an overpunch sign byte.

    Note the trap: in EBCDIC the letters A-I are byte-identical to positive
    overpunch digits (0xC1-0xC9). A one-byte 'zoned field' with no digit run in
    front of it is almost always just a letter, so require length >= 2.
    """
    digit_rate, sign_rate = [], []
    for c in cols:
        if not c.n:
            digit_rate.append(0.0); sign_rate.append(0.0); continue
        if encoding == "ebcdic":
            d = sum(v for b, v in c.hist.items() if 0xF0 <= b <= 0xF9)
            g = sum(v for b, v in c.hist.items()
                    if (b >> 4) in (0xC, 0xD) and (b & 0x0F) <= 9)
        else:
            d = sum(v for b, v in c.hist.items() if 0x30 <= b <= 0x39)
            g = sum(v for b, v in c.hist.items()
                    if b in codecs._ASCII_OVERPUNCH_POS or b in codecs._ASCII_OVERPUNCH_NEG)
        digit_rate.append(d / c.n); sign_rate.append(g / c.n)

    runs, i, n = [], 0, len(cols)
    while i < n:
        if sign_rate[i] >= threshold:
            start = i
            while start > 0 and digit_rate[start - 1] >= threshold:
                start -= 1
            if i - start >= 1:
                sample = [r[start:i + 1] for r in records[:2000]]
                if codecs.conformance(sample, lambda v: codecs.is_zoned(v, encoding)) >= threshold:
                    runs.append((start, i + 1))
        i += 1
    return runs


def segment(cols: list[ColumnStats], records: list[bytes], encoding: str) -> list[Field]:
    cmap = class_map(encoding)
    packed = find_packed_runs(cols, records)
    packed_cols = {i for s, e in packed for i in range(s, e)}
    zoned = [r for r in find_zoned_runs(cols, records, encoding)
             if not (set(range(*r)) & packed_cols)]
    zoned_cols = {i for s, e in zoned for i in range(s, e)}
    packed_cols |= zoned_cols

    fields, run_start, run_sig = [], None, None

    def flush(end):
        if run_start is not None:
            fields.append(Field(run_start, end))

    for i, c in enumerate(cols):
        if i in packed_cols:
            flush(i)
            run_start, run_sig = None, None
            continue
        sig = c.dominant_class(cmap)
        if run_sig is None:
            run_start, run_sig = i, sig
        elif sig != run_sig:
            flush(i)
            run_start, run_sig = i, sig
    flush(len(cols))

    for s, e in packed:
        if (s, e) not in zoned:
            fields.append(Field(s, e, kind="packed-decimal"))
    for s, e in zoned:
        fields.append(Field(s, e, kind="zoned-decimal"))
    fields.sort(key=lambda f: f.start)
    return merge_text_runs(fields, records, encoding, cols)


def _text_like(t: str) -> bool:
    """A single left-justified text field: content, then trailing padding, and
    nothing after the padding starts. Two fields glued together fail this,
    because the second field's content resumes after the first one's padding."""
    body = t.rstrip(" ")
    if not body or t.startswith(" "):
        return False
    return re.search(r"\s{2,}\S", body) is None


def _is_pure_digit(f: Field, cols: list[ColumnStats], cmap: dict,
                   threshold: float = 0.98) -> bool:
    return all(c.class_purity(cmap) >= threshold
               and c.dominant_class(cmap) == CLASS_DIGIT
               for c in cols[f.start:f.end])


def _is_hard_filler(f: Field, cols: list[ColumnStats], cmap: dict) -> bool:
    return f.length >= 3 and all(c.is_constant and c.dominant_class(cmap) == CLASS_SPACE
                                 for c in cols[f.start:f.end])


def merge_text_runs(fields: list[Field], records: list[bytes], encoding: str,
                    cols: list[ColumnStats], threshold: float = 0.95) -> list[Field]:
    """Character-class transitions over-segment text.

    A name column is alpha, then a comma, then alpha, then trailing spaces --
    four segments for one field. Merge them back, but only where the combined
    region behaves like one padded text field across the corpus.

    Where a merge crosses an internal class transition, that column is recorded
    as a candidate split. Some of those boundaries are genuinely undecidable
    from data alone -- 'status code then reference' and 'one 22-char reference'
    look identical in the bytes. The copybook resolves it; the data cannot. So
    report the ambiguity rather than guessing at it.
    """
    dec = "cp037" if encoding == "ebcdic" else "latin-1"
    cmap = class_map(encoding)
    out, i = [], 0
    while i < len(fields):
        cur = fields[i]
        # A fixed-width all-digit run is self-delimiting -- it never carries
        # padding, so it is its own field and must not seed a text merge.
        # A wide run of constant spaces is filler and must not be absorbed.
        if (cur.kind in ("packed-decimal", "zoned-decimal")
                or _is_pure_digit(cur, cols, cmap)
                or _is_hard_filler(cur, cols, cmap)):
            out.append(cur); i += 1; continue
        best, j = i, i
        while (j + 1 < len(fields)
               and fields[j + 1].kind not in ("packed-decimal", "zoned-decimal")
               and not _is_hard_filler(fields[j + 1], cols, cmap)):
            vals = [r[cur.start:fields[j + 1].end] for r in records[:3000]]
            txt = [v.decode(dec, errors="replace") for v in vals]
            if not any(ch.isalpha() for t in txt[:300] for ch in t):
                break
            if sum(1 for t in txt if _text_like(t)) / max(len(txt), 1) >= threshold:
                best = j + 1
            j += 1
        if best > i:
            merged = Field(cur.start, fields[best].end)
            merged.candidate_splits = [fields[k].end + 1 for k in range(i, best)]
            out.append(merged)
            i = best + 1
        else:
            out.append(cur); i += 1
    return out


def find_separators(f: Field, records: list[bytes], encoding: str,
                    threshold: float = 0.95) -> list[int]:
    """An interior column that is almost always a space, with non-space on both
    sides in the same record, is a separator inside one declared field. That is
    an overloaded field: the copybook says one thing, the data holds two."""
    space = 0x40 if encoding == "ebcdic" else 0x20
    hits = []
    for c in range(f.start + 1, f.end - 1):
        n = ok = 0
        for r in records[:3000]:
            n += 1
            if r[c] == space and r[c - 1] != space and any(
                    b != space for b in r[c + 1:f.end]):
                ok += 1
        if n and ok / n >= threshold:
            hits.append(c + 1)
    return hits


# --- classification -----------------------------------------------------------

def _null_analogs(values: list[bytes], length: int, encoding: str) -> dict:
    space = 0x40 if encoding == "ebcdic" else 0x20
    zero = 0xF0 if encoding == "ebcdic" else 0x30
    nine = 0xF9 if encoding == "ebcdic" else 0x39
    patterns = {
        "all_spaces": bytes([space]) * length,
        "all_zeros": bytes([zero]) * length,
        "all_nines": bytes([nine]) * length,
        "low_values": b"\x00" * length,
        "high_values": b"\xff" * length,
    }
    n = len(values) or 1
    out = {}
    counts = Counter(values)
    for name, pat in patterns.items():
        c = counts.get(pat, 0)
        if c:
            out[name] = round(c / n, 4)
    return out


def _date_fit(strings: list[str]) -> tuple[str, float] | None:
    if not strings:
        return None
    L = len(strings[0])
    ok = 0

    def valid_ymd(y, m, d):
        return 1900 <= y <= 2099 and 1 <= m <= 12 and 1 <= d <= 31

    if L == 8:
        for s in strings:
            if s.isdigit() and valid_ymd(int(s[:4]), int(s[4:6]), int(s[6:8])):
                ok += 1
        return ("YYYYMMDD", ok / len(strings))
    if L == 6:
        for s in strings:
            if s.isdigit() and valid_ymd(2000 + int(s[:2]), int(s[2:4]), int(s[4:6])):
                ok += 1
        return ("YYMMDD", ok / len(strings))
    if L == 5:
        for s in strings:
            if s.isdigit() and 1 <= int(s[2:]) <= 366:
                ok += 1
        return ("YYDDD (julian)", ok / len(strings))
    return None


def classify(f: Field, records: list[bytes], cols: list[ColumnStats], encoding: str) -> Field:
    cmap = class_map(encoding)
    values = [r[f.start:f.end] for r in records]
    f.cardinality = len(set(values))
    f.nulls = _null_analogs(values, f.length, encoding)

    seg_cols = cols[f.start:f.end]
    per_col = [c.dominant_class(cmap) for c in seg_cols]
    # Majority vote is wrong here: "REF5417 B558518" is mostly digits but is
    # plainly text. Any alpha-dominant column settles it.
    dom = CLASS_ALPHA if CLASS_ALPHA in per_col else Counter(per_col).most_common(1)[0][0]

    if f.kind == "packed-decimal":
        f.conformance = codecs.conformance(values, codecs.is_packed)
        digits = f.length * 2 - 1
        f.detail = f"COMP-3, {digits} digits"
        nums = [codecs.decode_packed(v) for v in values]
        nums = [x for x in nums if x is not None]
        if any(x < 0 for x in nums):
            f.notes.append("signed (negative values present)")
        mad = codecs.benford_fit(nums)
        if mad is not None:
            f.notes.append(f"{codecs.benford_verdict(mad)}  [MAD={mad}]")
        f.samples = [str(x) for x in nums[:4]]
        return f

    if all(c.dominant_class(cmap) == CLASS_SPACE and c.is_constant for c in seg_cols):
        f.kind, f.detail, f.conformance = "filler", "constant spaces", 1.0
        return f

    zoned_rate = codecs.conformance(values, lambda v: codecs.is_zoned(v, encoding))
    signed_rate = codecs.conformance(values, lambda v: codecs.zoned_is_signed(v, encoding))
    if zoned_rate >= 0.98 and signed_rate > 0.05:
        f.kind = "zoned-decimal"
        f.detail = f"signed overpunch, {f.length} digits"
        f.conformance = zoned_rate
        nums = [codecs.decode_zoned(v, encoding) for v in values]
        nums = [x for x in nums if x is not None]
        mad = codecs.benford_fit(nums)
        if mad is not None:
            f.notes.append(f"{codecs.benford_verdict(mad)}  [MAD={mad}]")
        f.notes.append(f"{signed_rate:.1%} of records carry a negative/positive zone")
        f.samples = [str(x) for x in nums[:4]]
        return f

    text = [codecs_decode(v, encoding) for v in values]

    if dom == CLASS_DIGIT:
        f.kind = "numeric-text"
        f.conformance = codecs.conformance(
            values, lambda v: all(cmap.get(b) == CLASS_DIGIT for b in v))
        digits_only = [t for t in text if t.isdigit()]
        fit = _date_fit(digits_only)
        if fit and fit[1] > 0.90:
            f.kind = "date"
            f.detail = f"{fit[0]}, {fit[1]:.1%} parse cleanly"
        else:
            f.detail = f"{f.length} digits"
            if digits_only:
                nums = [int(t) for t in digits_only]
                if _is_sequence(nums):
                    f.notes.append("monotonically increasing -> sequence/key")
                mad = codecs.benford_fit(nums)
                if mad is not None:
                    f.notes.append(f"{codecs.benford_verdict(mad)}  [MAD={mad}]")
        f.samples = text[:4]
        return f

    if dom in (CLASS_ALPHA, CLASS_SPACE):
        f.kind = "text"
        f.detail = f"{f.length} chars"
        if f.cardinality <= 25 and len(records) > 200:
            f.kind = "code"
            top = Counter(text).most_common(5)
            f.detail = f"{f.cardinality} distinct values"
            f.notes.append("low cardinality -> lookup code, needs a domain mapping")
            f.samples = [f"{v.strip()!r} x{c}" for v, c in top]
            return f
        seps = find_separators(f, records, encoding)
        if seps:
            f.notes.append(
                f"OVERLOADED: consistent separator at column(s) "
                f"{', '.join(map(str, seps))} -- one declared field, multiple values")
        f.samples = [t.rstrip() for t in text[:4]]
        return f

    f.kind = "mixed"
    f.detail = f"dominant class {dom}"
    f.samples = [v.hex() for v in values[:3]]
    return f


def codecs_decode(v: bytes, encoding: str) -> str:
    return v.decode("cp037" if encoding == "ebcdic" else "latin-1", errors="replace")


def _is_sequence(nums: list[int], threshold: float = 0.95) -> bool:
    if len(nums) < 50:
        return False
    inc = sum(1 for a, b in zip(nums, nums[1:]) if b > a)
    return inc / (len(nums) - 1) >= threshold


# --- drift --------------------------------------------------------------------

def detect_drift(records: list[bytes], cols: list[ColumnStats], encoding: str,
                 chunks: int = 10) -> list[dict]:
    """Find columns whose character profile changes partway through the file.

    This catches the single most common surprise in a historical extract: the
    format changed when the system was upgraded, and records either side of
    that point follow different rules.
    """
    cmap = class_map(encoding)
    n = len(records)
    if n < chunks * 50:
        return []
    size = n // chunks
    findings = []
    for i in range(len(cols)):
        profile, shares = [], []
        for c in range(chunks):
            seg = records[c * size:(c + 1) * size]
            counts = Counter(cmap.get(r[i], "other") for r in seg)
            top, n_top = counts.most_common(1)[0]
            profile.append(top)
            shares.append({k: v / len(seg) for k, v in counts.items()})
        if len(set(profile)) < 2:
            continue
        # Only report where the winning class's share actually moves a lot.
        # Otherwise near-ties in noisy text columns flip the argmax at random.
        change = None
        for k in range(1, chunks):
            if profile[k] == profile[k - 1]:
                continue
            drop = shares[k - 1].get(profile[k - 1], 0) - shares[k].get(profile[k - 1], 0)
            if drop >= 0.25:
                change = k
                break
        if change is None:
            continue
        findings.append({
            "column": i + 1,
            "before": profile[change - 1],
            "after": profile[change],
            "approx_record": change * size,
            "pct_through": round(100 * change / chunks),
        })
    return findings
