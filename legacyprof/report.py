"""Render the profile. Terminal output for humans, JSON for the next stage,
HTML for the human who needs to see the layout rather than read it.

The JSON is the important one -- it is the machine-readable spec that a
downstream tool diffs against the copybook, and that a model reads to propose
semantics. The bytes never leave; this does.

The HTML report is one self-contained file: inline CSS and JS, no fonts, no
CDN, no external reference of any kind. It gets opened on the same locked-down
machines the CLI runs on, where a page that fetches a stylesheet is an
outbound connection and therefore a compliance incident. It renders whatever
the profile holds; whether field values are included is decided upstream, at
the emission boundary in cli.py, never here.
"""

import json
from html import escape
from pathlib import PureWindowsPath

KIND_WIDTH = 16


def render_text(profile: dict) -> str:
    L = []
    meta = profile["file"]
    L.append(f"file            {meta['path']}")
    L.append(f"size            {meta['size']:,} bytes")
    L.append(f"encoding        {meta['encoding']}  (confidence {meta['encoding_confidence']})")
    L.append(f"record length   {meta['reclen']}  via {meta['reclen_method']}")
    L.append(f"records         {meta['records']:,}  ({meta['sampled']:,} sampled)")
    L.append("")
    L.append(f"{'cols':>9}  {'len':>3}  {'kind':<{KIND_WIDTH}} {'conf':>6}  detail")
    L.append("-" * 78)

    for f in profile["fields"]:
        conf = f"{f['conformance']:.1%}" if f["conformance"] else "-"
        L.append(f"{f['columns']:>9}  {f['length']:>3}  {f['kind']:<{KIND_WIDTH}} "
                 f"{conf:>6}  {f['detail']}")
        if f.get("candidate_splits"):
            L.append(f"{'':>9}  {'':>3}  ?  merged over class boundaries at column(s) "
                     f"{', '.join(map(str, f['candidate_splits']))} "
                     f"-- undecidable without the copybook")
        for note in f["notes"]:
            L.append(f"{'':>9}  {'':>3}  -> {note}")
        if f["nulls"]:
            nulls = ", ".join(f"{k} {v:.1%}" for k, v in f["nulls"].items())
            L.append(f"{'':>9}  {'':>3}  -> null analogs: {nulls}")
        if f["samples"]:
            L.append(f"{'':>9}  {'':>3}  e.g. {', '.join(str(s) for s in f['samples'][:3])}")

    if profile["drift"]:
        L.append("")
        L.append("distribution drift -- the layout changes partway through this file")
        L.append("-" * 78)
        for d in profile["drift"]:
            L.append(f"  column {d['column']:>3}: {d['before']} -> {d['after']} "
                     f"at ~record {d['approx_record']:,} ({d['pct_through']}% through)")

    if profile["warnings"]:
        L.append("")
        L.append("warnings")
        L.append("-" * 78)
        for w in profile["warnings"]:
            L.append(f"  {w}")

    return "\n".join(L)


def render_json(profile: dict) -> str:
    return json.dumps(profile, indent=2)


# --- HTML ---------------------------------------------------------------------

# Kinds fold into three colour families. Any two fields can sit side by side on
# the map, so every pair of colours must stay distinguishable under colour
# blindness -- and only three categorical hues clear that bar for all pairs.
# The kind itself is always written out (map label, table), so colour is never
# the only carrier of identity.
_FAMILY = {
    "text": "text", "code": "text",
    "numeric-text": "digits", "date": "digits",
    "packed-decimal": "decimal", "zoned-decimal": "decimal",
    "filler": "filler",
}
_FAMILY_LEGEND = [
    ("text", "Text and codes"),
    ("digits", "Digits and dates"),
    ("decimal", "Packed and zoned decimal"),
    ("filler", "Filler"),
    ("other", "Unclassified"),
]
# Short enough to fit inside a narrow block on the map; the table gives the
# full kind.
_SHORT_KIND = {"numeric-text": "digits", "packed-decimal": "COMP-3",
               "zoned-decimal": "zoned"}
_RECLEN_METHOD = {
    "entropy-minimisation": "inferred from column entropy",
    "line-terminated": "from line terminators",
    "user-specified": "set with --reclen",
}

_DARK_TOKENS = (
    "color-scheme:dark;"
    "--bg:#1a1a19;--ink:#f2f1ec;--ink-2:#c3c2b7;--muted:#9a988f;"
    "--hair:#2c2c2a;--axis:#4a4a46;--hl:#262624;--leak-bg:#3a1f1f;"
    "--k-text:#3987e5;--t-text:#213246;"
    "--k-digits:#199e70;--t-digits:#1a372c;"
    "--k-decimal:#d95926;--t-decimal:#44281c;"
    "--k-filler:#5a5954;--t-filler:#292927;"
    "--k-other:#898781;--t-other:#30302d;"
)

_HTML_CSS = (
    ":root{color-scheme:light;"
    "--bg:#fcfcfb;--ink:#1a1c20;--ink-2:#52514e;--muted:#6f6d68;"
    "--hair:#e1e0d9;--axis:#c3c2b7;--hl:#f0efe9;--leak-bg:#fbe7e7;"
    "--crit:#d03b3b;"
    "--k-text:#2a78d6;--t-text:#dae7f5;"
    "--k-digits:#1baf7a;--t-digits:#d8f0e6;"
    "--k-decimal:#eb6834;--t-decimal:#f9e4db;"
    "--k-filler:#c3c2b7;--t-filler:#efeee9;"
    "--k-other:#898781;--t-other:#e4e3dd}"
    "@media (prefers-color-scheme:dark){:root:not([data-theme=\"light\"]){"
    + _DARK_TOKENS + "}}"
    ":root[data-theme=\"dark\"]{" + _DARK_TOKENS + "}"
    "*{box-sizing:border-box}"
    "body{margin:0;padding-inline:1rem;background:var(--bg);color:var(--ink);"
    "font:15px/1.5 \"Segoe UI Variable Text\",\"Segoe UI\",system-ui,-apple-system,sans-serif}"
    "main{max-width:68rem;margin:0 auto;padding-block:2.5rem 4rem}"
    "h1{margin:0;font-size:clamp(1.75rem,4.5vw,2.75rem);line-height:1.1;"
    "font-weight:650;letter-spacing:-.02em;overflow-wrap:anywhere}"
    "h2{margin:3rem 0 .35rem;font-size:1.2rem;font-weight:650}"
    ".path{margin:.35rem 0 0;color:var(--muted);font-size:.85rem;overflow-wrap:anywhere}"
    ".facts{display:grid;grid-template-columns:repeat(auto-fit,minmax(10rem,1fr));"
    "gap:1rem 2rem;margin:1.75rem 0 0;padding-block:1rem;border-block:1px solid var(--hair)}"
    ".facts div{min-width:0}"
    ".facts dt{color:var(--muted);font-size:.8rem}"
    ".facts dd{margin:0;font-size:1.15rem;font-weight:600}"
    ".facts small{display:block;font-size:.8rem;font-weight:400;color:var(--ink-2)}"
    ".privacy{margin:1rem 0 0;max-width:70ch;color:var(--ink-2);font-size:.875rem}"
    ".privacy.leak{color:var(--ink);background:var(--leak-bg);"
    "border-left:3px solid var(--crit);padding:.6rem .8rem}"
    ".lede{margin:0 0 1.25rem;max-width:70ch;color:var(--ink-2)}"
    # The record map. Minor ticks mark every byte boundary, like the column
    # ruler on a punch card, but only while there are few enough to read.
    ".map{position:relative}"
    ".ruler{position:relative;height:1.6rem}"
    ".ruler.fine{background:repeating-linear-gradient(to right,var(--axis) 0 1px,"
    "transparent 1px calc(100% / var(--cols))) left bottom/100% 5px no-repeat}"
    ".tick{position:absolute;top:0;display:flex;justify-content:center;"
    "font-size:.72rem;color:var(--muted);font-variant-numeric:tabular-nums;white-space:nowrap}"
    ".bar{position:relative;height:3.25rem}"
    ".field{position:absolute;top:0;bottom:0;text-decoration:none}"
    ".fill{position:absolute;inset:0 1px;background:var(--t);border-bottom:3px solid var(--k)}"
    ".field:hover .fill{box-shadow:inset 0 0 0 1px var(--k)}"
    ".field:focus-visible{outline:2px solid var(--ink);outline-offset:2px;z-index:4}"
    ".lbl{position:absolute;top:0;bottom:3px;z-index:3;display:flex;flex-direction:column;"
    "justify-content:center;align-items:flex-start;padding:0 calc(.3rem + 1px);"
    "overflow:hidden;pointer-events:none;container-type:inline-size;color:var(--ink)}"
    ".lbl .k,.lbl .c{background:var(--t);padding-right:2px;white-space:nowrap}"
    ".k{font-size:.8rem;font-weight:600;line-height:1.2}"
    ".c{font-size:.72rem;color:var(--ink-2);font-variant-numeric:tabular-nums}"
    # Hide labels that would not fit rather than clip them; the table has them.
    # The query measures the label's content box, and 3.1rem is the widest
    # short kind ("COMP-3") at this size.
    "@container (max-width:3.1rem){.lbl .k,.lbl .c{display:none}}"
    ".split{position:absolute;top:-4px;bottom:-4px;width:0;z-index:2;"
    "border-left:2px dashed var(--ink);transform:translateX(-1px)}"
    ".drift-row{position:relative;height:.9rem;margin-top:.35rem}"
    ".drift-mark{position:absolute;top:0;bottom:0;min-width:2px;background:var(--crit)}"
    ".readout{min-height:1.5em;margin:.9rem 0 0;font-size:.9rem}"
    ".legend{display:flex;flex-wrap:wrap;gap:.4rem 1.25rem;margin:.9rem 0 0;padding:0;"
    "list-style:none;font-size:.82rem;color:var(--ink-2)}"
    ".legend li{display:flex;align-items:center;gap:.4rem}"
    ".sw{display:inline-block;flex:none;width:.9rem;height:.9rem;"
    "background:var(--t);border-bottom:3px solid var(--k);vertical-align:-2px;margin-right:.4rem}"
    ".legend .sw{margin:0}"
    ".sw-split{width:0;height:.9rem;border-left:2px dashed var(--ink)}"
    ".sw-drift{width:.9rem;height:.45rem;background:var(--crit)}"
    ".fam-text{--k:var(--k-text);--t:var(--t-text)}"
    ".fam-digits{--k:var(--k-digits);--t:var(--t-digits)}"
    ".fam-decimal{--k:var(--k-decimal);--t:var(--t-decimal)}"
    ".fam-filler{--k:var(--k-filler);--t:var(--t-filler)}"
    ".fam-other{--k:var(--k-other);--t:var(--t-other)}"
    # Tables scroll inside their own box so the page never scrolls sideways.
    ".table-wrap{overflow-x:auto}"
    "table{width:100%;min-width:40rem;border-collapse:collapse;font-size:.9rem}"
    "th{padding:.5rem .75rem .5rem 0;border-bottom:1px solid var(--axis);text-align:left;"
    "font-size:.8rem;font-weight:600;color:var(--ink-2)}"
    "td{padding:.7rem .75rem .7rem 0;border-bottom:1px solid var(--hair);vertical-align:top}"
    "tr:target td{background:var(--hl)}"
    ".num{font-variant-numeric:tabular-nums;white-space:nowrap}"
    ".kind{white-space:nowrap}"
    ".rate{white-space:nowrap;font-variant-numeric:tabular-nums}"
    ".meter{display:inline-block;width:4.5rem;height:6px;margin-right:.5rem;overflow:hidden;"
    "border-radius:3px;background:var(--hair);vertical-align:middle}"
    ".meter span{display:block;height:100%;background:var(--ink-2)}"
    ".sub{display:block;font-size:.78rem;color:var(--muted);white-space:normal}"
    ".obs p{margin:0}"
    ".obs ul{margin:.35rem 0 0;padding-left:1.1rem;font-size:.85rem;color:var(--ink-2)}"
    ".muted{color:var(--muted)}"
    ".plain{margin:0;padding-left:1.1rem}"
    "footer{margin-top:3.5rem;padding-top:1rem;border-top:1px solid var(--hair);"
    "font-size:.8rem;color:var(--muted)}"
)

# Hover and keyboard focus both read out into one line under the map. The
# table carries the same information, so the page still works with scripts off.
_HTML_JS = (
    "(function(){"
    "var out=document.getElementById('readout');if(!out)return;"
    "var idle=out.textContent;"
    "var marks=document.querySelectorAll('.bar .field,.bar .split,.drift-mark');"
    "Array.prototype.forEach.call(marks,function(el){"
    "function show(){out.textContent=el.getAttribute('aria-label');}"
    "function hide(){out.textContent=idle;}"
    "el.addEventListener('mouseenter',show);el.addEventListener('focus',show);"
    "el.addEventListener('mouseleave',hide);el.addEventListener('blur',hide);"
    "});})();"
)


def _e(value) -> str:
    return escape(str(value), quote=True)


def _pct(n: float, reclen: int) -> str:
    """Byte offset as a percentage of the record, formatted identically on
    every run so the file is byte-for-byte reproducible."""
    return f"{100 * n / reclen:.4f}".rstrip("0").rstrip(".") + "%"


def _cols(f: dict) -> str:
    return f"{f['start']}–{f['end']}"


def render_html(profile: dict) -> str:
    """Return the profile as one self-contained HTML document."""
    meta = profile["file"]
    name = PureWindowsPath(meta["path"]).name or meta["path"]
    parts = [
        "<!doctype html>",
        '<html lang="en">',
        "<head>",
        '<meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        f"<title>{_e(name)} layout profile</title>",
        f"<style>{_HTML_CSS}</style>",
        "</head>",
        "<body>",
        "<main>",
        _html_header(profile, name),
        _html_map(profile),
        _html_fields(profile),
        _html_drift(profile["drift"]),
        _html_warnings(profile["warnings"]),
        f"<footer>Generated by legacyprof from {meta['sampled']:,} of "
        f"{meta['records']:,} records. Re-running it on the same file produces "
        f"this exact report.</footer>",
        "</main>",
        f"<script>{_HTML_JS}</script>",
        "</body>",
        "</html>",
        "",
    ]
    return "\n".join(parts)


def _html_header(profile: dict, name: str) -> str:
    meta = profile["file"]
    method = _RECLEN_METHOD.get(meta["reclen_method"], meta["reclen_method"])
    facts = [
        ("Size", f"{meta['size']:,} bytes", ""),
        ("Encoding", meta["encoding"].upper(), f"confidence {meta['encoding_confidence']}"),
        ("Record length", f"{meta['reclen']:,} bytes", method),
        ("Records", f"{meta['records']:,}", f"{meta['sampled']:,} profiled"),
    ]
    dl = "".join(
        f"<div><dt>{_e(k)}</dt><dd>{_e(v)}"
        + (f"<small>{_e(s)}</small>" if s else "") + "</dd></div>"
        for k, v, s in facts)
    if any(f["samples"] for f in profile["fields"]):
        privacy = ('<p class="privacy leak">This report contains raw field values '
                   "because it was run with --samples. Handle it with the same care "
                   "as the source file.</p>")
    else:
        privacy = ('<p class="privacy">This report holds column statistics and '
                   "rates only. It contains no field values.</p>")
    return (f"<header><h1>{_e(name)}</h1>"
            f'<p class="path">{_e(meta["path"])}</p>'
            f'<dl class="facts">{dl}</dl>{privacy}</header>')


def _html_ruler(reclen: int) -> str:
    step = next((s for s in (1, 2, 5, 10, 20, 25, 50, 100, 250, 500, 1000, 2500)
                 if reclen / s <= 10), 5000)
    cols = sorted({1, *range(step, reclen + 1, step)})
    ticks = "".join(
        f'<span class="tick" style="left:{_pct(c - 1, reclen)};'
        f'width:{_pct(1, reclen)}">{c}</span>' for c in cols)
    fine = " fine" if reclen <= 200 else ""
    return f'<div class="ruler{fine}" aria-hidden="true">{ticks}</div>'


def _html_map(profile: dict) -> str:
    R = profile["file"]["reclen"]
    fields, drift = profile["fields"], profile["drift"]
    out = ['<section aria-labelledby="h-map">',
           '<h2 id="h-map">Record layout</h2>',
           f'<p class="lede">Each block is one inferred field, drawn on the bytes '
           f"it occupies in a {R:,}-byte record. Select a block to jump to its "
           f"details.</p>",
           f'<div class="map" style="--cols:{R}">',
           _html_ruler(R),
           '<div class="bar">']
    for f in fields:
        fam = _FAMILY.get(f["kind"], "other")
        say = f"Columns {_cols(f)}, {f['kind']}" + (f": {f['detail']}" if f["detail"] else "")
        out.append(
            f'<a class="field fam-{fam}" data-cols="{_e(f["columns"])}" '
            f'href="#f-{_e(f["columns"])}" '
            f'style="left:{_pct(f["start"] - 1, R)};width:{_pct(f["length"], R)}" '
            f'aria-label="{_e(say)}"><span class="fill"></span></a>')
    for f in fields:
        for c in f["candidate_splits"]:
            # A split at column c is a possible boundary on c's left edge.
            out.append(
                f'<span class="split" data-col="{c}" style="left:{_pct(c - 1, R)}" '
                f'role="img" aria-label="Possible boundary before column {c}. '
                f'The bytes cannot settle it; the copybook can."></span>')
    # Labels sit on their own layer above the split marks, so a dashed line
    # passes behind the text instead of striking through it. They ignore the
    # pointer, so hover still reaches the block or split underneath.
    for f in fields:
        fam = _FAMILY.get(f["kind"], "other")
        out.append(
            f'<span class="lbl fam-{fam}" aria-hidden="true" '
            f'style="left:{_pct(f["start"] - 1, R)};width:{_pct(f["length"], R)}">'
            f'<span class="k">{_e(_SHORT_KIND.get(f["kind"], f["kind"]))}</span>'
            f'<span class="c">{_cols(f)}</span></span>')
    out.append("</div>")
    if drift:
        out.append('<div class="drift-row">')
        for d in drift:
            c = d["column"]
            out.append(
                f'<span class="drift-mark" data-col="{c}" '
                f'style="left:{_pct(c - 1, R)};width:{_pct(1, R)}" role="img" '
                f'aria-label="Column {c} changes from {_e(d["before"])} to '
                f'{_e(d["after"])} near record {d["approx_record"]:,}"></span>')
        out.append("</div>")
    out.append("</div>")
    out.append('<p class="readout" id="readout" aria-live="polite">'
               "Hover over a block, or move to it with Tab, to read it here.</p>")

    present = {_FAMILY.get(f["kind"], "other") for f in fields}
    legend = [f'<li><span class="sw fam-{fam}"></span>{label}</li>'
              for fam, label in _FAMILY_LEGEND if fam in present]
    if any(f["candidate_splits"] for f in fields):
        legend.append('<li><span class="sw-split"></span>Possible boundary '
                      "the copybook must settle</li>")
    if drift:
        legend.append('<li><span class="sw-drift"></span>Column changes format '
                      "partway through the file</li>")
    out.append(f'<ul class="legend">{"".join(legend)}</ul>')
    out.append("</section>")
    return "\n".join(out)


def _html_rate(conf: float, sampled: int) -> str:
    # 0.0 means no codec check applies to this kind (free text), not "0% fit".
    if not conf:
        return '<td class="rate"><span class="muted">not measured</span></td>'
    pct = f"{conf:.1%}"
    if conf < 1 and pct == "100.0%":
        pct = ">99.9%"  # never round a failing record away
    bad = round((1 - conf) * sampled)
    sub = ""
    if conf < 1 and bad:
        sub = (f'<span class="sub">about {bad:,} '
               f'{"record does" if bad == 1 else "records do"} not conform</span>')
    return (f'<td class="rate"><span class="meter"><span style="width:{conf * 100:.2f}%">'
            f"</span></span>{_e(pct)}{sub}</td>")


def _html_fields(profile: dict) -> str:
    sampled = profile["file"]["sampled"]
    rows = []
    for f in profile["fields"]:
        fam = _FAMILY.get(f["kind"], "other")
        items = [_e(n) for n in f["notes"]]
        if f["candidate_splits"]:
            *head, last = map(str, f["candidate_splits"])
            where = f"column {last}" if not head else f"columns {', '.join(head)} and {last}"
            items.append(f"Possible boundary before {where}. "
                         "The bytes cannot settle it; the copybook can.")
        if f["nulls"]:
            items.append("Placeholder values: " + ", ".join(
                f"{_e(k.replace('_', ' '))} {v:.1%}" for k, v in f["nulls"].items()))
        if f["samples"]:
            items.append("Examples: " + ", ".join(_e(s) for s in f["samples"][:3]))
        obs = f"<p>{_e(f['detail'])}</p>" if f["detail"] else ""
        if items:
            obs += "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"
        rows.append(
            f'<tr id="f-{_e(f["columns"])}"><td class="num">{_cols(f)}</td>'
            f'<td class="num">{f["length"]}</td>'
            f'<td class="kind"><span class="sw fam-{fam}"></span>{_e(f["kind"])}</td>'
            f'{_html_rate(f["conformance"], sampled)}'
            f'<td class="obs">{obs}</td></tr>')
    return ('<section aria-labelledby="h-fields"><h2 id="h-fields">Fields</h2>'
            '<p class="lede">Conformance is the share of profiled records that '
            "pass the kind's byte-level check. The records that fail are usually "
            "the ones worth finding.</p>"
            '<div class="table-wrap"><table><thead><tr><th>Columns</th><th>Length</th>'
            "<th>Kind</th><th>Conformance</th><th>What the data shows</th></tr></thead>"
            f'<tbody>{"".join(rows)}</tbody></table></div></section>')


def _html_drift(drift: list) -> str:
    head = ('<section aria-labelledby="h-drift">'
            '<h2 id="h-drift">Where the layout changes</h2>')
    if not drift:
        return (head + '<p class="lede">No column changes character class partway '
                "through the file.</p></section>")
    rows = "".join(
        f'<tr><td class="num">{d["column"]}</td><td>{_e(d["before"])}</td>'
        f'<td>{_e(d["after"])}</td><td class="num">{d["approx_record"]:,}</td>'
        f'<td class="num">{d["pct_through"]}%</td></tr>' for d in drift)
    return (head + '<p class="lede">These columns switch character class partway '
            "through the file. That usually means the source system changed format, "
            "and records on either side follow different rules.</p>"
            '<div class="table-wrap"><table><thead><tr><th>Column</th><th>Before</th>'
            "<th>After</th><th>Starts near record</th><th>Through the file</th></tr>"
            f"</thead><tbody>{rows}</tbody></table></div></section>")


def _html_warnings(warnings: list) -> str:
    body = ('<ul class="plain">' + "".join(f"<li>{_e(w)}</li>" for w in warnings) + "</ul>"
            if warnings else '<p class="lede">None.</p>')
    return f'<section aria-labelledby="h-warn"><h2 id="h-warn">Warnings</h2>{body}</section>'
