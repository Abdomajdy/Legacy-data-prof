# CLAUDE.md

Instructions for working in this repository.

## What this is

`legacyprof` infers the layout of fixed-width legacy data extracts (mainframe
COBOL output, NACHA, BAI2) when the copybook is missing, and reports where the
real data disagrees with the copybook when it isn't.

Read `PROJECT.md` for why the project exists and what it is not. Read
`README.md` for user-facing behaviour.

## Commands

```bash
python3 fixtures/generate.py                          # build the test corpus (5 MB, gitignored)
python3 -m legacyprof.cli fixtures/customer_master.dat # run the profiler
python3 tests/score.py                                 # score against ground truth
python3 -m unittest discover -s tests                  # report + privacy tests
python3 -m legacyprof.cli fixtures/customer_master.dat --html out.html  # visual report
```

The HTML report is the only frontend, and it is a file, not a server. It
inherits every hard constraint below: nothing in it may reference anything
outside itself.

There is no build step and no install step. That is deliberate — see
constraints below.

## Hard constraints

These are not style preferences. Breaking any of them breaks the product.

**No network calls. Ever.** Not for telemetry, not for version checks, not for
model inference. This tool runs inside hospital and bank networks where a
single outbound connection is a compliance incident. Anything needing a network
belongs in a separate, clearly-named package.

**No dependencies outside the standard library.** The target machine is often a
locked-down jump box with no pip access and an ancient Python. Every dependency
added is a deployment that doesn't happen. If you think you need numpy, write
the loop.

**Raw field values never leave by default.** `--samples` is opt-in and must stay
opt-in. The JSON profile is designed to be safe to send off-site: distributions,
conformance rates, and counts, never values. When adding a field to the profile
output, ask whether it could reconstruct a record. Min/max values leak.
Cardinality-1 fields leak. Top-N value lists leak.

**Deterministic.** Same input bytes produce the same profile, byte for byte. No
randomness, no iteration over unordered sets where order affects output, no
time-dependent behaviour. Downstream this feeds reconciliation pipelines where
a differing result between two runs is an audit finding.

**Python 3.10+.** The code uses `X | None` union syntax.

## Design invariants

**Conformance rates, never booleans.** Every codec check returns a fraction in
[0, 1] measured against the corpus. A field that is 99.7% packed decimal is
packed decimal with 300 corrupt records, and those 300 records are the entire
reason someone is running this tool. Never collapse a rate to a boolean in the
output. Thresholds are for internal branching only.

**Report ambiguity, do not resolve it.** Some boundaries are genuinely
undecidable from bytes alone — a 2-char code followed by a 20-char reference is
byte-identical to one 22-char reference. Emit `candidate_splits` and let the
copybook settle it. Silently guessing is worse than saying "I don't know",
because a wrong boundary propagates into every downstream field.

**Statistics are deterministic; interpretation is not.** Byte-level
classification (encoding, packed decimal, overpunch, entropy) is exact
constraint-checking and must never involve heuristic scoring beyond a
threshold. Semantic naming — "this is a routing number" — is a separate layer
that does not exist yet, and when it does it proposes, and the proposal gets
compiled into a check that runs here.

**Profile byte positions, not fields.** The whole point is that you don't know
where the fields are. Any code that assumes a known layout is in the wrong
module.

## Testing

`tests/score.py` is the only measure that counts. Current baseline:

```
exact boundary + kind : 5/9   (56%)
boundary recovered    : 7/9   (78%)
format change @30,000 : FOUND, 0 false positives
date sentinel         : FOUND, 8.14%
```

**Never tune a heuristic to make the fixture pass.** The fixture has 50,000
records with nine known pathologies; it is a smoke test, not the target. If a
change improves the score by special-casing something present in
`customer_master.dat`, it has made the tool worse on real files. The correct
move when you find a new failure mode is to add a *new* fixture with that
pathology, then fix the algorithm generically.

**Always re-run the score before and after.** Heuristics interact. Three of the
four bugs found in the first build session were invisible by inspection and
only showed up as a score regression.

## Adding things

**A new codec sniffer** goes in `codecs.py` as a pure function
`(bytes) -> bool` plus an optional `decode_*`. Wire it into `fields.classify`
behind a conformance check. Add a field using it to the fixture generator and
extend the ground truth JSON in the same commit.

**A new pathology detector** goes in `fields.py` and appends to `Field.notes`.
Notes are prose shown to a human; they must state what was observed and at what
rate, not what to do about it.

**A new format family** (HL7 v2, X12) gets its own package that reuses
`codecs.py` and the pathology engine. Do not generalise `fields.py` to handle
delimited formats — segmentation for self-delimiting formats is a different
problem and merging them produces something that does neither well.

## Style

Comments explain *why*, particularly why a threshold is what it is or why an
obvious approach fails. The domain is full of traps that look like bugs
(EBCDIC letters A-I are byte-identical to positive overpunch digits; chi-square
scales with n and rejects real amounts at 50k records). Write those down at the
site — they will be rediscovered otherwise.

Module docstrings carry the algorithm explanation. Function docstrings carry
the contract.

No `print` outside `report.py` and `cli.py`.
