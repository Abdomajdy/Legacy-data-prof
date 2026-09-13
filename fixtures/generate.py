"""Generate a synthetic mainframe extract with known ground truth.

You cannot get real customer files -- they are PHI, PII, or financial records
and nobody is uploading them. So the eval corpus has to be synthetic, with
pathologies injected deliberately so you can measure whether the profiler
finds them. This file IS the test oracle.

Layout (100-byte fixed records, EBCDIC cp037, no line terminators):
  1-20    customer name          text, space padded
  21-26   birth date             YYMMDD, 8% sentinel '000000'
  27-31   balance                COMP-3 packed decimal, 9 digits, signed
  32-41   adjustment             zoned decimal, overpunch sign
  42-49   account number         8 digits, sequential
  50-51   status code            2 chars, 6 distinct values
  52-71   customer reference     OVERLOADED: two space-separated tokens
  72-79   last activity date     FORMAT CHANGE at 60%: YYMMDD+2sp -> YYYYMMDD
  80-100  filler                 constant spaces
"""

import json
import random
import sys
from pathlib import Path

RECLEN = 100
N_RECORDS = 50_000
SEED = 20260911

FIRST = ["JAMES", "MARY", "ROBERT", "PATRICIA", "JOHN", "JENNIFER", "MICHAEL",
         "LINDA", "DAVID", "ELIZABETH", "WILLIAM", "BARBARA", "RICHARD", "SUSAN"]
LAST = ["SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "GARCIA", "MILLER",
        "DAVIS", "RODRIGUEZ", "MARTINEZ", "HERNANDEZ", "LOPEZ", "WILSON"]
STATUS = ["AC", "IN", "SU", "CL", "PN", "DQ"]


def ebcdic(s: str, width: int) -> bytes:
    return s.ljust(width)[:width].encode("cp037")


def packed(value: int, nbytes: int) -> bytes:
    """COMP-3: two digits per byte, sign nibble last."""
    digits = nbytes * 2 - 1
    sign = 0x0D if value < 0 else 0x0C
    s = str(abs(value)).rjust(digits, "0")[-digits:]
    out = bytearray()
    for i in range(0, digits - 1, 2):
        out.append((int(s[i]) << 4) | int(s[i + 1]))
    out.append((int(s[-1]) << 4) | sign)
    return bytes(out)


def zoned_ebcdic(value: int, width: int) -> bytes:
    """Zoned decimal with the sign folded into the final byte's zone nibble."""
    s = str(abs(value)).rjust(width, "0")[-width:]
    out = bytearray(0xF0 | int(c) for c in s)
    out[-1] = ((0x0D if value < 0 else 0x0C) << 4) | int(s[-1])
    return bytes(out)


def amount_like(rng) -> int:
    """Exponentially distributed -> obeys Benford's law, like real money."""
    return int(rng.expovariate(1 / 40_000) * 100)


def build(path: Path, n: int = N_RECORDS) -> dict:
    rng = random.Random(SEED)
    switch_at = int(n * 0.6)
    out = bytearray()

    for i in range(n):
        rec = bytearray()

        name = f"{rng.choice(LAST)}, {rng.choice(FIRST)}"
        rec += ebcdic(name, 20)

        if rng.random() < 0.08:
            rec += ebcdic("000000", 6)                     # sentinel: unknown
        else:
            rec += ebcdic(f"{rng.randint(40, 99):02d}"
                          f"{rng.randint(1, 12):02d}"
                          f"{rng.randint(1, 28):02d}", 6)

        bal = amount_like(rng) * (-1 if rng.random() < 0.12 else 1)
        rec += packed(bal, 5)

        adj = amount_like(rng) // 10 * (-1 if rng.random() < 0.30 else 1)
        rec += zoned_ebcdic(adj, 10)

        rec += ebcdic(f"{10_000_000 + i:08d}", 8)          # sequential key
        rec += ebcdic(rng.choice(STATUS), 2)

        # Overloaded field: documented as one 20-char reference, actually two
        tok1 = f"REF{rng.randint(1000, 9999)}"
        tok2 = f"{rng.choice('ABCDEFGH')}{rng.randint(100000, 999999)}"
        rec += ebcdic(f"{tok1} {tok2}", 20)

        # Format change partway through history
        if i < switch_at:
            rec += ebcdic(f"{rng.randint(10, 24):02d}"
                          f"{rng.randint(1, 12):02d}"
                          f"{rng.randint(1, 28):02d}  ", 8)
        else:
            rec += ebcdic(f"20{rng.randint(24, 26):02d}"
                          f"{rng.randint(1, 12):02d}"
                          f"{rng.randint(1, 28):02d}", 8)

        rec += ebcdic("", 21)
        assert len(rec) == RECLEN, len(rec)
        out += rec

    path.write_bytes(bytes(out))

    truth = {
        "reclen": RECLEN,
        "records": n,
        "encoding": "ebcdic",
        "codepage": "cp037",
        "fields": [
            {"start": 1, "end": 20, "kind": "text", "name": "customer name"},
            {"start": 21, "end": 26, "kind": "date", "name": "birth date",
             "pathology": "8% sentinel 000000"},
            {"start": 27, "end": 31, "kind": "packed-decimal", "name": "balance"},
            {"start": 32, "end": 41, "kind": "zoned-decimal", "name": "adjustment"},
            {"start": 42, "end": 49, "kind": "numeric-text", "name": "account number",
             "pathology": "sequential key"},
            {"start": 50, "end": 51, "kind": "code", "name": "status"},
            {"start": 52, "end": 71, "kind": "text", "name": "customer reference",
             "pathology": "overloaded: two tokens"},
            {"start": 72, "end": 79, "kind": "date", "name": "last activity",
             "pathology": f"format change at record {switch_at}"},
            {"start": 80, "end": 100, "kind": "filler", "name": "filler"},
        ],
        "format_change_record": switch_at,
    }
    return truth


if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else N_RECORDS
    here = Path(__file__).parent
    truth = build(here / "customer_master.dat", n)
    (here / "customer_master.truth.json").write_text(json.dumps(truth, indent=2))
    print(f"wrote {n} records ({n * RECLEN:,} bytes) + ground truth")
