# PROJECT.md

## The problem

Hospitals, banks, insurers, ports, and government agencies run on systems built
in the 1980s and 90s. Getting data in or out of them is still done largely by
hand. No amount of progress elsewhere in software has touched this, because the
work is unglamorous, per-customer, and requires reading specifications nobody
wants to read.

The specific pain this project targets: **a legacy integration is roughly 20%
writing a parser and 80% discovering that the real data violates the documented
layout.** The copybook says a field is a date; 12% of values are `000000`. The
copybook says one 20-character customer reference; the data holds two
space-separated tokens because someone needed somewhere to put them in 1998.
The layout changed in 2014 when the source system was upgraded, and records on
either side follow different rules.

Today that discovery phase takes weeks of an expensive person staring at hex
dumps. This tool automates it.

## What we are building

A local CLI that reads a fixed-width extract and emits a machine-readable
profile: inferred record length, encoding, field boundaries, codecs, and a list
of every place the data does something the spec would not predict.

The profiler is phase one. The actual product is the **diff** — declared layout
from the copybook against observed reality from the data. The profiler exists
to produce one side of that comparison.

## What we are not building

**Not a SaaS.** Mainframe extracts are PHI, PII, or financial records. No bank
or hospital will upload them to a startup's cloud, and no amount of SOC 2 will
change that within the time we have. This constraint is good: it is precisely
why a generic cloud data-quality tool cannot take this market.

**Not an autonomous parser.** The tool compresses a six-week discovery phase
into a few days and makes the remaining human judgment auditable. It does not
remove the human. Pitching it as automation gets it rejected by exactly the
buyers who understand the domain.

**Not a general-purpose data quality tool.** Great Expectations and Soda exist
and are good. The value here is depth of legacy-specific edge case handling —
EBCDIC code pages, COMP-3, overpunch signs, implied decimals, REDEFINES — which
does not generalise and is not worth trying to.

**Not cross-format on day one.** Depth in one format family is defensible.
Breadth across five is shallow everywhere, and the edge-case knowledge does not
transfer between fixed-width and HL7.

## Who this is for

Immediate: integration engineers and systems integrators doing mainframe
modernisation, healthcare interfacing, or bank core migrations. Small
population, technically sophisticated, will judge the tool in ten minutes.

Where the budget actually is: the enterprises themselves, reached through
services engagements. The open-source CLI is the credibility and the lead
generator, not the business. That should stay clear-eyed — tooling for
integrators is a small market on its own.

## Roadmap

**Phase 1 — profiler** *(current)*
Fixed-width / COBOL copybook family. Encoding, record length, boundary
inference, codec detection, pathology reporting. Open source.

**Phase 2 — copybook parser**
COBOL copybook → the same JSON schema shape the profiler emits. PIC clauses,
COMP/COMP-3, `REDEFINES`, `OCCURS DEPENDING ON`, implied decimal positions,
level-88 condition names. This is mechanical and well-specified.

**Phase 3 — the diff**
Declared vs observed. This is the product. Output is a report an analyst signs
off on: every divergence, its rate, and the records that exhibit it.

**Phase 4 — second format family**
HL7 v2 or X12, sharing the pathology engine. HL7 v2 has the better ratio of
learnable-in-a-month to permanently-in-demand; X12 has more trading-partner
deviation to catch. Decide based on which inbound is stronger by then.

**Phase 5 — hypothesis layer**
A model reads the documentation (PDF implementation guides, scanned copybooks)
and the profile *statistics* — never the records — and proposes field
semantics. Every proposal compiles to an executable check that runs locally
against the full corpus. Confidence comes from the pass rate, never from the
model's self-report. Human sign-off is a recorded product feature, not a
limitation: regulated buyers need to show why a field was interpreted a given
way.

## Decision log

**Fixed-width first, not HL7 or X12.** It sits underneath several other formats
(NACHA, BAI2, most mainframe extracts), it has the richest pathology set, and
it is the only family where the profiler can do something genuinely striking —
recover the layout with no spec at all. HL7 and X12 are self-delimiting, so
boundary inference there is trivial and value only appears at the diff stage.

**Statistics cross the privacy boundary, records never do.** A character-class
histogram is not PHI. This is what lets a future hypothesis layer use a
frontier model without the data leaving the building. Redaction policy must be
enforced at the profile-emission boundary, not left to the caller.

**Benford via MAD, not chi-square.** Chi-square scales with sample size; at
50,000 records it rejects genuine monetary fields. Mean absolute deviation is
sample-size independent. Nigrini's thresholds apply.

**Synthetic fixtures as the eval oracle.** Real customer files are unobtainable.
Generating files with deliberately injected pathologies and known ground truth
is not a workaround — it is better, because the ground truth is exact and the
corpus can grow to cover every failure mode encountered in the field without
ever holding customer data.

**Ambiguity is reported, not resolved.** See CLAUDE.md.

## Open questions

- Does the diff output need to be a signed artifact for audit purposes, or is a
  reproducible JSON plus a git hash enough for the buyers we care about?
- RECFM=VB (RDW-prefixed variable-length) is detected but not parsed. How often
  does it show up in practice relative to fixed-block?
- Is there a licensing model that keeps the profiler open while making the
  accumulated rule library — the actual moat — commercial without fragmenting
  the community?
- What is the smallest credible services offering that this tool makes
  deliverable in under two weeks?
