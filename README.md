# legacyprof

Infers the layout of a fixed-width legacy extract when you don't have the copybook,
and tells you where the data disagrees with the copybook when you do.

Runs entirely locally. No network calls. The bytes never leave the machine — the
JSON profile it emits contains distributions and conformance rates, not values,
unless you explicitly pass `--samples`.

## Why

A legacy integration is about 20% writing a parser and 80% discovering that the
real data violates the documented layout. This tool automates the 80%.

## Usage

```bash
python3 -m legacyprof.cli path/to/extract.dat
python3 -m legacyprof.cli path/to/extract.dat --json > profile.json
python3 -m legacyprof.cli path/to/extract.dat --reclen 240 --encoding ebcdic
python3 -m legacyprof.cli path/to/extract.dat --html report.html
```

`--html` writes a single self-contained page: a byte ruler of the record with
every inferred field drawn on its columns, conformance rates, possible
boundaries, and the columns whose format changes partway through the file. It
has no external references at all -- no fonts, no CDN -- so it opens offline on
the same locked-down machine, and like the JSON it holds no field values unless
you pass `--samples` (in which case the page says so at the top).

## What it detects

**Structure**
- EBCDIC vs ASCII, from a byte histogram
- Record length, by minimising per-column entropy across candidate lengths —
  at the right length every column aligns and entropy collapses
- RECFM=VB (RDW-prefixed) files, flagged but not yet parsed
- Field boundaries, from character-class transitions per byte position

**Codecs** — each reported with a conformance rate against the full corpus,
never a boolean. A field that is 99.7% packed decimal is packed decimal with
300 bad records, and those are the ones that break the load at 3am.
- COMP-3 packed decimal, including sign nibble
- Zoned decimal with overpunch sign (EBCDIC C/D zones, ASCII `{A-I` / `}J-R`)
- Numeric text, dates (`YYYYMMDD`, `YYMMDD`, `YYDDD` julian)

**Pathologies**
- Null analogs: spaces, zeros, all-nines, low-values, high-values
- Sentinel values masquerading as data (`000000` in a date field)
- Overloaded fields — one declared field holding two space-separated tokens
- Sequence/key fields (monotonically increasing)
- Amount vs identifier, via Benford's law on the leading digit (MAD, not
  chi-square — chi-square scales with n and rejects everything at 50k records)
- **Distribution drift**: the layout changing partway through the file, which
  is what happens when the source system was upgraded in 2014 and the records
  either side follow different rules

## Current accuracy

Against `fixtures/customer_master.dat` (50,000 EBCDIC records, nine fields with
deliberately injected pathologies):

```
exact boundary + kind : 5/9   (56%)
boundary recovered    : 7/9   (78%)
format change @30,000 : FOUND, 0 false positives
date sentinel         : FOUND, 8.14%
overloaded field      : FOUND, separator at column 59
```

Reproduce with `python3 fixtures/generate.py && python3 tests/score.py`.

## Known limitations

These are real and worth stating plainly.

- **Some boundaries are undecidable from data alone.** A 2-char status code
  followed by a 20-char reference is byte-identical to one 22-char reference.
  The profiler merges them and reports `candidate_splits` rather than guessing.
  The copybook resolves it; the data cannot.
- **A field whose format changes mid-file won't classify cleanly**, because it
  genuinely isn't one format. Drift detection flags it instead.
- **COMP (binary integer) detection is weak.** A 4-byte binary field is hard to
  distinguish from text without a spec.
- **EBCDIC letters A-I are byte-identical to positive overpunch digits.** A
  one-byte "signed field" is almost always just a letter, so zoned runs require
  a preceding digit run.
- Single code page assumed (cp037). Mixed-code-page files will mis-decode.
- **Record-length inference mis-infers small files.** On a 1,000-record
  version of the fixture, entropy minimisation picks 4,000 instead of 100.
  Plug-in entropy is biased low when fewer records are measured, and a long
  candidate length has fewer records, so it looks artificially well aligned.
  Pass `--reclen` until this is fixed generically.

## Roadmap

1. Copybook parser → machine-readable schema (PIC clauses, REDEFINES, OCCURS
   DEPENDING ON, implied decimals)
2. Spec-vs-reality diff: declared layout against observed profile. This is the
   actual product — the profiler is the input to it.
3. HL7 v2 and X12 as second format families, sharing the pathology engine
4. Hypothesis layer over the profile JSON, with every proposal compiled into an
   executable check and scored by pass rate on the full corpus

## Layout

```
legacyprof/
  encoding.py   EBCDIC vs ASCII detection
  records.py    record length inference, RDW detection
  columns.py    per-byte-position statistics
  codecs.py     packed/zoned/binary sniffers, Benford
  fields.py     boundary inference, classification, drift
  report.py     terminal, JSON and offline HTML rendering
  cli.py        orchestration
fixtures/
  generate.py   synthetic corpus + ground truth (the test oracle)
tests/
  score.py              accuracy against ground truth
  test_report_html.py   HTML report + sample-privacy boundary
```
