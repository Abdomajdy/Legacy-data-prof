"""Detect the byte encoding of a flat file.

The only distinction that matters in practice is EBCDIC vs ASCII/Latin-1.
Both are single-byte, so the decision falls out of a byte histogram: printable
text clusters in completely different ranges in each encoding.
"""

from collections import Counter

# EBCDIC (cp037) printable ranges
_EBCDIC_LOWER = set(range(0x81, 0x8A)) | set(range(0x91, 0x9A)) | set(range(0xA2, 0xAA))
_EBCDIC_UPPER = set(range(0xC1, 0xCA)) | set(range(0xD1, 0xDA)) | set(range(0xE2, 0xEA))
_EBCDIC_DIGIT = set(range(0xF0, 0xFA))
_EBCDIC_SPACE = {0x40}
EBCDIC_PRINTABLE = _EBCDIC_LOWER | _EBCDIC_UPPER | _EBCDIC_DIGIT | _EBCDIC_SPACE

# ASCII printable
ASCII_PRINTABLE = set(range(0x20, 0x7F))

# Code pages worth trying once EBCDIC is established. cp037 is US/Canada and
# by far the most common; the others differ in where currency and bracket
# characters live, which matters for fields holding symbols.
EBCDIC_CODEPAGES = ["cp037", "cp500", "cp1047", "cp273", "cp277", "cp280", "cp285"]


def byte_histogram(data: bytes) -> Counter:
    return Counter(data)


def detect_encoding(data: bytes, sample_bytes: int = 1_000_000) -> dict:
    """Return {'encoding', 'confidence', 'scores'} for a byte sample."""
    sample = data[:sample_bytes]
    if not sample:
        return {"encoding": "unknown", "confidence": 0.0, "scores": {}}

    hist = byte_histogram(sample)
    total = len(sample)

    ebcdic_hits = sum(c for b, c in hist.items() if b in EBCDIC_PRINTABLE)
    ascii_hits = sum(c for b, c in hist.items() if b in ASCII_PRINTABLE)

    ebcdic_score = ebcdic_hits / total
    ascii_score = ascii_hits / total

    # Padding is the strongest single tell. Mainframe records pad with 0x40,
    # ASCII files pad with 0x20. Whichever dominates usually settles it.
    pad_ebcdic = hist.get(0x40, 0) / total
    pad_ascii = hist.get(0x20, 0) / total

    scores = {
        "ebcdic_printable_ratio": round(ebcdic_score, 4),
        "ascii_printable_ratio": round(ascii_score, 4),
        "x40_ratio": round(pad_ebcdic, 4),
        "x20_ratio": round(pad_ascii, 4),
    }

    if ebcdic_score > ascii_score and pad_ebcdic >= pad_ascii:
        enc, conf = "ebcdic", ebcdic_score - ascii_score
    elif ascii_score > ebcdic_score:
        enc, conf = "ascii", ascii_score - ebcdic_score
    else:
        enc, conf = "ambiguous", 0.0

    return {"encoding": enc, "confidence": round(min(conf * 2, 1.0), 3), "scores": scores}


def to_text(data: bytes, encoding: str, codepage: str = "cp037") -> str:
    """Decode for display only. Never use this before field boundaries are
    known -- binary fields (packed decimal, comp) will mangle."""
    if encoding == "ebcdic":
        return data.decode(codepage, errors="replace")
    return data.decode("latin-1", errors="replace")
