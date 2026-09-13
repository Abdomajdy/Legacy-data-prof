"""Per-byte-position statistics.

Everything downstream is built on this. The key idea is to profile *byte
positions*, not fields -- because you don't know where the fields are yet.
Field boundaries fall out of where the column character-class profile changes.
"""

import math
from collections import Counter
from dataclasses import dataclass, field as dc_field

# Character classes, evaluated in the encoding the file is actually in.
CLASS_DIGIT = "digit"
CLASS_ALPHA = "alpha"
CLASS_SPACE = "space"
CLASS_NUL = "nul"
CLASS_HIGH = "high"
CLASS_PUNCT = "punct"
CLASS_OTHER = "other"

_ASCII_SETS = {
    CLASS_DIGIT: set(range(0x30, 0x3A)),
    CLASS_ALPHA: set(range(0x41, 0x5B)) | set(range(0x61, 0x7B)),
    CLASS_SPACE: {0x20},
    CLASS_NUL: {0x00},
    CLASS_HIGH: {0xFF},
}
_EBCDIC_SETS = {
    CLASS_DIGIT: set(range(0xF0, 0xFA)),
    CLASS_ALPHA: (set(range(0x81, 0x8A)) | set(range(0x91, 0x9A)) | set(range(0xA2, 0xAA))
                  | set(range(0xC1, 0xCA)) | set(range(0xD1, 0xDA)) | set(range(0xE2, 0xEA))),
    CLASS_SPACE: {0x40},
    CLASS_NUL: {0x00},
    CLASS_HIGH: {0xFF},
}
_PUNCT = {
    "ascii": set(range(0x21, 0x30)) | set(range(0x3A, 0x41)) | set(range(0x5B, 0x61)),
    "ebcdic": {0x4B, 0x4E, 0x5B, 0x5C, 0x5D, 0x60, 0x61, 0x6B, 0x6C, 0x6D, 0x6E, 0x7A, 0x7E},
}


def class_map(encoding: str) -> dict:
    sets = _EBCDIC_SETS if encoding == "ebcdic" else _ASCII_SETS
    m = {}
    for cls, byteset in sets.items():
        for b in byteset:
            m[b] = cls
    for b in _PUNCT.get(encoding, _PUNCT["ascii"]):
        m.setdefault(b, CLASS_PUNCT)
    return m


@dataclass
class ColumnStats:
    index: int
    hist: Counter = dc_field(default_factory=Counter)
    n: int = 0

    @property
    def distinct(self) -> int:
        return len(self.hist)

    @property
    def entropy(self) -> float:
        h = 0.0
        for c in self.hist.values():
            p = c / self.n
            h -= p * math.log2(p)
        return h

    @property
    def is_constant(self) -> bool:
        return self.distinct == 1

    def class_profile(self, cmap: dict) -> Counter:
        prof = Counter()
        for b, c in self.hist.items():
            prof[cmap.get(b, CLASS_OTHER)] += c
        return prof

    def dominant_class(self, cmap: dict) -> str:
        prof = self.class_profile(cmap)
        return prof.most_common(1)[0][0] if prof else CLASS_OTHER

    def class_purity(self, cmap: dict) -> float:
        prof = self.class_profile(cmap)
        if not prof:
            return 0.0
        return prof.most_common(1)[0][1] / self.n


def profile_columns(records, reclen: int, limit: int | None = None) -> tuple[list[ColumnStats], int]:
    cols = [ColumnStats(i) for i in range(reclen)]
    n = 0
    for rec in records:
        if limit is not None and n >= limit:
            break
        if len(rec) != reclen:
            continue
        for i, b in enumerate(rec):
            cols[i].hist[b] += 1
        n += 1
    for c in cols:
        c.n = n
    return cols, n
