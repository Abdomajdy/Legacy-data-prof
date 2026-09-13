"""legacyprof -- infer the layout of a fixed-width legacy extract.

Runs entirely locally. No network calls, no data leaves the machine. The JSON
profile it emits contains distributions and conformance rates, never raw field
values, unless you pass --samples.
"""

import argparse
import sys
from pathlib import Path

from . import columns, fields, records, report
from .encoding import detect_encoding


def profile_file(path: Path, sample: int = 100_000, reclen: int | None = None,
                 encoding: str | None = None, include_samples: bool = False) -> dict:
    data = path.read_bytes()
    warnings = []

    enc = detect_encoding(data)
    enc_name = encoding or enc["encoding"]
    if enc["encoding"] == "ambiguous" and not encoding:
        warnings.append("encoding is ambiguous; pass --encoding to force it")
        enc_name = "ascii"

    if reclen:
        rec_info = {"reclen": reclen, "method": "user-specified", "terminator": None}
    else:
        rec_info = records.detect_reclen(data)
        if rec_info["reclen"] is None:
            raise SystemExit(f"could not determine record length: {rec_info['method']}")

    R = rec_info["reclen"]
    if len(data) % R and not rec_info.get("terminator"):
        warnings.append(f"file size {len(data)} is not a multiple of {R} -- "
                        f"trailing {len(data) % R} bytes ignored")

    recs = list(records.iter_records(data, R, rec_info.get("terminator")))
    total = len(recs)
    sampled = recs[:sample]

    cols, n = columns.profile_columns(iter(sampled), R)
    segs = fields.segment(cols, sampled, enc_name)
    segs = [fields.classify(f, sampled, cols, enc_name) for f in segs]
    segs = _flag_composites(segs, warnings)
    drift = fields.detect_drift(sampled, cols, enc_name)

    return {
        "file": {
            "path": str(path),
            "size": len(data),
            "encoding": enc_name,
            "encoding_confidence": enc["confidence"],
            "encoding_scores": enc["scores"],
            "reclen": R,
            "reclen_method": rec_info["method"],
            "records": total,
            "sampled": n,
        },
        "fields": [{
            "columns": f.label,
            "start": f.start + 1,
            "end": f.end,
            "length": f.length,
            "kind": f.kind,
            "detail": f.detail,
            "conformance": round(f.conformance, 4),
            "cardinality": f.cardinality,
            "nulls": f.nulls,
            "notes": f.notes,
            "candidate_splits": f.candidate_splits,
            "samples": f.samples if include_samples else [],
        } for f in segs],
        "drift": drift,
        "warnings": warnings,
    }


def _flag_composites(segs: list, warnings: list) -> list:
    """A one-column constant-space segment wedged between two data segments is
    a separator inside what the copybook calls a single field."""
    for i in range(1, len(segs) - 1):
        prev, cur, nxt = segs[i - 1], segs[i], segs[i + 1]
        if cur.kind == "filler" and cur.length <= 2 \
                and prev.kind not in ("filler",) and nxt.kind not in ("filler",):
            note = (f"columns {prev.start + 1}-{nxt.end} look like ONE declared field "
                    f"holding two tokens separated at column {cur.label}")
            prev.notes.append(note)
            warnings.append("possible overloaded field: " + note)
    return segs


def main(argv=None):
    p = argparse.ArgumentParser(prog="legacyprof", description=__doc__)
    p.add_argument("path", type=Path)
    p.add_argument("--reclen", type=int, help="skip inference, force record length")
    p.add_argument("--encoding", choices=["ascii", "ebcdic"], help="force encoding")
    p.add_argument("--sample", type=int, default=100_000, help="records to profile")
    p.add_argument("--json", action="store_true", help="emit machine-readable profile")
    p.add_argument("--html", type=Path, metavar="PATH",
                   help="also write a self-contained offline HTML report to PATH")
    p.add_argument("--samples", action="store_true",
                   help="include example field values (LEAKS DATA -- off by default)")
    a = p.parse_args(argv)

    # Decided once, at the emission boundary, so every output mode inherits it.
    # Stripping per renderer is how the text report came to leak values.
    prof = profile_file(a.path, sample=a.sample, reclen=a.reclen,
                        encoding=a.encoding, include_samples=a.samples)
    if a.html:
        # Bytes, not write_text: text mode on Windows emits \r\n, and the same
        # input must produce the same file on every machine.
        a.html.write_bytes(report.render_html(prof).encode("utf-8"))
    print(report.render_json(prof) if a.json else report.render_text(prof))
    return 0


if __name__ == "__main__":
    sys.exit(main())
