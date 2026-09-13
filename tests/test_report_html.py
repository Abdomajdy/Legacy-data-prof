"""Tests for the offline HTML report and the sample-privacy boundary.

The HTML report is the tool's only visual frontend, and it inherits every hard
constraint the CLI has: it must open with no network (it is read inside the
same locked-down networks), carry no field values unless --samples was passed,
and be byte-identical for identical input.

Run with: python -m unittest discover -s tests
"""

import copy
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "fixtures"))

import generate  # noqa: E402
from legacyprof import cli, report  # noqa: E402


def _field(start, end, kind, **kw):
    """One profile field, with every key cli.profile_file emits."""
    f = {"columns": f"{start}-{end}", "start": start, "end": end,
         "length": end - start + 1, "kind": kind, "detail": "",
         "conformance": 0.0, "cardinality": 0, "nulls": {}, "notes": [],
         "candidate_splits": [], "samples": []}
    f.update(kw)
    return f


PROFILE = {
    "file": {
        "path": "extract.dat", "size": 100_000, "encoding": "ebcdic",
        "encoding_confidence": 0.9,
        "encoding_scores": {"ebcdic_printable_ratio": 0.95, "ascii_printable_ratio": 0.5,
                            "x40_ratio": 0.3, "x20_ratio": 0.0},
        "reclen": 100, "reclen_method": "entropy-minimisation",
        "records": 1000, "sampled": 1000,
    },
    "fields": [
        _field(1, 20, "text", detail="20 chars"),
        _field(21, 26, "date", detail="YYMMDD, 91.9% parse cleanly",
               conformance=0.9971, nulls={"all_zeros": 0.0814}),
        _field(27, 66, "text", detail="40 chars", candidate_splits=[52],
               notes=["found <b>two</b> tokens"]),
        _field(67, 100, "filler", detail="constant spaces", conformance=1.0),
    ],
    "drift": [{"column": 30, "before": "digit", "after": "space",
               "approx_record": 600, "pct_through": 60}],
    "warnings": ["possible overloaded field: columns 27-66"],
}


class _Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def _tags(html):
    p = _Tags()
    p.feed(html)
    return p.tags


def _with_class(html, cls):
    return [a for _, a in _tags(html) if cls in (a.get("class") or "").split()]


def _geometry(attrs):
    """'left:20%;width:6%' -> {'left': 20.0, 'width': 6.0}"""
    out = {}
    for decl in (attrs.get("style") or "").split(";"):
        if ":" in decl:
            k, v = decl.split(":", 1)
            if v.strip().endswith("%"):
                out[k.strip()] = float(v.strip()[:-1])
    return out


class RecordMapGeometry(unittest.TestCase):
    """The map is only useful if each mark sits on the columns it claims.
    An off-by-one here puts a boundary on the wrong byte, which is the exact
    error the tool exists to prevent."""

    def setUp(self):
        self.html = report.render_html(PROFILE)

    def test_each_field_block_spans_exactly_its_columns(self):
        blocks = {a["data-cols"]: _geometry(a) for a in _with_class(self.html, "field")}
        want = {"1-20": (0, 20), "21-26": (20, 6), "27-66": (26, 40), "67-100": (66, 34)}
        self.assertEqual(set(blocks), set(want))
        for cols, (left, width) in want.items():
            self.assertAlmostEqual(blocks[cols]["left"], left, places=3, msg=cols)
            self.assertAlmostEqual(blocks[cols]["width"], width, places=3, msg=cols)

    def test_candidate_split_sits_on_the_left_edge_of_its_column(self):
        splits = _with_class(self.html, "split")
        self.assertEqual(len(splits), 1)
        self.assertAlmostEqual(_geometry(splits[0])["left"], 51, places=3)

    def test_drift_marker_covers_its_column(self):
        marks = _with_class(self.html, "drift-mark")
        self.assertEqual(len(marks), 1)
        g = _geometry(marks[0])
        self.assertAlmostEqual(g["left"], 29, places=3)
        self.assertAlmostEqual(g["width"], 1, places=3)


class ReportContent(unittest.TestCase):
    def setUp(self):
        self.html = report.render_html(PROFILE)

    def test_conformance_is_shown_as_a_rate(self):
        # 99.71% conformance means ~3 bad records in 1000. Rounding it to a
        # pass mark would hide exactly the records the analyst is looking for.
        self.assertIn("99.7%", self.html)
        self.assertIn("about 3 records", self.html)

    def test_near_perfect_conformance_never_displays_as_100(self):
        # 2 bad records in 50,000 is 99.996%, which .1% formatting rounds to
        # "100.0%" -- and those 2 records are what the analyst came for.
        prof = copy.deepcopy(PROFILE)
        prof["file"]["sampled"] = 50_000
        prof["fields"][1]["conformance"] = 0.99996
        html = report.render_html(prof)
        row = html[html.index('id="f-21-26"'):]
        row = row[:row.index("</tr>")]
        self.assertNotIn("100.0%", row)
        self.assertIn("&gt;99.9%", row)
        self.assertIn("about 2 records", row)

    def test_profile_text_is_escaped(self):
        self.assertIn("found &lt;b&gt;two&lt;/b&gt; tokens", self.html)
        self.assertNotIn("<b>two</b>", self.html)

    def test_page_references_nothing_outside_itself(self):
        # Opening the report must not make an outbound connection -- inside a
        # hospital network that is a compliance incident, same as the CLI.
        self.assertNotIn("http", self.html.lower())
        self.assertNotIn("@import", self.html)
        self.assertEqual([a for _, a in _tags(self.html) if "src" in a], [])
        hrefs = [a["href"] for _, a in _tags(self.html) if "href" in a]
        self.assertTrue(hrefs, "fields should link from the map to the table")
        self.assertTrue(all(h.startswith("#") for h in hrefs), hrefs)

    def test_same_profile_renders_identically(self):
        self.assertEqual(report.render_html(copy.deepcopy(PROFILE)), self.html)


class SampleBoundary(unittest.TestCase):
    """Raw values leave the machine only with --samples, in every output mode.
    Driven through the real CLI against a real generated extract."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.dat = Path(cls.tmp.name) / "extract.dat"
        generate.build(cls.dat, 1000)
        # The first record's customer name, decoded independently of the tool.
        cls.name = cls.dat.read_bytes()[:20].decode("cp037").rstrip()

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def _run(self, *args):
        # --reclen is pinned so these tests exercise the privacy boundary, not
        # record-length inference (which mis-infers on small files -- a
        # separate failure mode, see README known limitations).
        buf = io.StringIO()
        with redirect_stdout(buf):
            cli.main([str(self.dat), "--reclen", "100", *args])
        return buf.getvalue()

    def _html(self, *args):
        out = Path(self.tmp.name) / "report.html"
        self._run("--html", str(out), *args)
        return out.read_bytes()

    def test_text_report_holds_no_values_by_default(self):
        self.assertNotIn(self.name, self._run())

    def test_text_report_shows_values_with_samples(self):
        self.assertIn(self.name, self._run("--samples"))

    def test_html_report_holds_no_values_by_default(self):
        self.assertNotIn(self.name, self._html().decode("utf-8"))

    def test_html_report_shows_values_and_says_so_with_samples(self):
        html = self._html("--samples").decode("utf-8")
        self.assertIn(self.name, html)
        self.assertIn("contains raw field values", html)

    def test_html_file_is_byte_identical_across_runs(self):
        first = self._html()
        self.assertEqual(self._html(), first)
        # Text-mode writes on Windows turn \n into \r\n, so the same input
        # would give different bytes on different machines.
        self.assertNotIn(b"\r\n", first)


if __name__ == "__main__":
    unittest.main()
