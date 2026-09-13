"""Deterministic codec sniffers.

Each one takes the raw bytes of a candidate field across many records and
returns a conformance rate in [0, 1]. No guessing, no model -- these are
constraint checks, and the pass rate on the full corpus is the evidence.

The rate matters more than the boolean. A field that is 99.7% packed decimal
is packed decimal with 0.3% corrupt records, and those 300 records are the
ones that will break the load at 3am. Surfacing them is the whole point.
"""

import math
from collections import Counter

# --- packed decimal (COMP-3) --------------------------------------------------
# Two digits per byte, sign in the low nibble of the final byte.
# C/F = positive, D = negative, A/B/E = rare alternates.
_PACKED_SIGNS = {0xA, 0xB, 0xC, 0xD, 0xE, 0xF}


def is_packed(b: bytes) -> bool:
    if not b:
        return False
    for byte in b[:-1]:
        if (byte >> 4) > 9 or (byte & 0x0F) > 9:
            return False
    last = b[-1]
    return (last >> 4) <= 9 and (last & 0x0F) in _PACKED_SIGNS


def decode_packed(b: bytes) -> int | None:
    if not is_packed(b):
        return None
    digits = "".join(f"{byte >> 4}{byte & 0x0F}" for byte in b[:-1])
    digits += str(b[-1] >> 4)
    sign = -1 if (b[-1] & 0x0F) == 0xD else 1
    return sign * int(digits) if digits else 0


# --- zoned decimal with overpunch sign ---------------------------------------
# The sign is folded into the zone nibble of the last digit byte.
# EBCDIC: C0-C9 positive, D0-D9 negative, F0-F9 unsigned.
# ASCII:  {ABCDEFGHI positive, }JKLMNOPQR negative.
_ASCII_OVERPUNCH_POS = {ord("{"): 0, **{ord(c): i + 1 for i, c in enumerate("ABCDEFGHI")}}
_ASCII_OVERPUNCH_NEG = {ord("}"): 0, **{ord(c): i + 1 for i, c in enumerate("JKLMNOPQR")}}


def is_zoned(b: bytes, encoding: str) -> bool:
    if not b:
        return False
    if encoding == "ebcdic":
        if not all(0xF0 <= x <= 0xF9 for x in b[:-1]):
            return False
        last = b[-1]
        return (last >> 4) in (0xC, 0xD, 0xF) and (last & 0x0F) <= 9
    if not all(0x30 <= x <= 0x39 for x in b[:-1]):
        return False
    last = b[-1]
    return (0x30 <= last <= 0x39 or last in _ASCII_OVERPUNCH_POS
            or last in _ASCII_OVERPUNCH_NEG)


def zoned_is_signed(b: bytes, encoding: str) -> bool:
    """True if the trailing byte actually carries a sign rather than being a
    plain digit. This is what distinguishes a signed amount from an ID."""
    if not b:
        return False
    last = b[-1]
    if encoding == "ebcdic":
        return (last >> 4) in (0xC, 0xD)
    return last in _ASCII_OVERPUNCH_POS or last in _ASCII_OVERPUNCH_NEG


def decode_zoned(b: bytes, encoding: str) -> int | None:
    if not is_zoned(b, encoding):
        return None
    if encoding == "ebcdic":
        digits = "".join(str(x & 0x0F) for x in b)
        sign = -1 if (b[-1] >> 4) == 0xD else 1
        return sign * int(digits)
    last = b[-1]
    head = b[:-1].decode("ascii")
    if last in _ASCII_OVERPUNCH_NEG:
        return -int(head + str(_ASCII_OVERPUNCH_NEG[last]))
    if last in _ASCII_OVERPUNCH_POS:
        return int(head + str(_ASCII_OVERPUNCH_POS[last]))
    return int(head + chr(last))


# --- binary integer (COMP) ----------------------------------------------------
def plausible_binary(values: list[bytes]) -> float:
    """Weak test. A 2/4/8-byte field whose big-endian interpretation stays in a
    narrow band, and which is not valid text, is probably COMP."""
    if not values or len(values[0]) not in (2, 4, 8):
        return 0.0
    ints = [int.from_bytes(v, "big", signed=True) for v in values]
    if len(set(ints)) < 2:
        return 0.0
    span = max(ints) - min(ints)
    ceiling = 256 ** len(values[0])
    return 1.0 if span < ceiling // 16 else 0.3


# --- shape tests --------------------------------------------------------------
def conformance(values: list[bytes], predicate) -> float:
    if not values:
        return 0.0
    return sum(1 for v in values if predicate(v)) / len(values)


def benford_fit(numbers: list[int]) -> float | None:
    """Chi-square goodness of fit against Benford's law on the leading digit.

    Genuine measured or monetary quantities follow Benford. Identifiers,
    sequence numbers, and codes do not. This is the cheapest available signal
    for 'is this an amount or an account number', and it costs one pass.

    Returns mean absolute deviation of the leading-digit proportions, or None
    if there isn't enough data. Chi-square is the textbook test but it scales
    with n -- at 50,000 records it rejects everything, including real amounts.
    MAD is sample-size independent. Nigrini's thresholds: <0.006 close
    conformance, <0.012 acceptable, <0.015 marginal, above that nonconforming.
    """
    leads = [int(str(abs(n))[0]) for n in numbers if n and str(abs(n))[0] != "0"]
    if len(leads) < 200:
        return None
    observed = Counter(leads)
    n = len(leads)
    mad = sum(abs(observed.get(d, 0) / n - math.log10(1 + 1 / d))
              for d in range(1, 10)) / 9
    return round(mad, 5)


def benford_verdict(mad: float | None) -> str | None:
    if mad is None:
        return None
    if mad < 0.012:
        return "amount-like (Benford conforming)"
    if mad < 0.015:
        return "marginal Benford fit"
    return "identifier-like (not Benford)"
