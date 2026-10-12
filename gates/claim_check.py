# -*- coding: utf-8 -*-
"""claim-check — a figure in a client-facing deliverable must be readable at a cited file:line.

Born 2026-08-29 from a client-proposal defect: a 9-agent fan-out returned file PATHS with a
self-reported `verified: true`, and the orchestrator published on that word without
opening the files. Three claims went to print unsourced; one ("membresias de S/650 a
S/5,000 al mes") was simply false — the vendor sells S/599 / S/1,849 / S/2,399 one-off.

An agent's `verified` field is the check and the checked sharing an author. This is the
independent reader: it re-opens the cited line and looks for the figure itself.

  python claim-check.py <deliverable> <claims.json>
  python claim-check.py --selftest

claims.json = [{"figure": "44,131", "file": "C:/...md", "line": 217, "note": "optional"}]
  - `line` may be omitted to search the whole file (weaker, but still a real read).
  - Every figure appearing in the deliverable MUST have a claim, or it is UNSOURCED.

Exit 0 = every figure traced.  Exit 1 = a claim is false or a figure is unsourced.
Exit 2 = the gate could not run (no claims, unreadable source, planted probe missed).
Exit 2 is held distinct from 1 so "your document is wrong" and "my gate never ran"
stay separable — they want different repairs.
"""
import json
import pathlib
import re
import sys

# This box defaults to cp1252; anything reading this output (a pipe, a parent process,
# Claude) gets a UnicodeDecodeError on the first em dash without this.
try:
    sys.stdout.reconfigure(encoding="utf-8")
except (AttributeError, OSError):
    pass

# A figure worth sourcing: currency amounts, percentages, and bare numbers >= 3 digits
# or carrying a thousands separator / decimal. Deliberately NOT every integer — "12 meses"
# and "4 dias" are prose, not claims, and a gate that cries wolf gets switched off.
FIGURE = re.compile(
    r"(?<![\w.,])(?:S/\s?|US\$\s?|\$|€)?\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?%?"
    r"|(?<![\w.,])(?:S/\s?|US\$\s?|\$|€)\s?\d+(?:[.,]\d+)?%?"
    r"|(?<![\w.,])\d+(?:[.,]\d+)?%"
    r"|(?<![\w.,])\d{3,}(?:[.,]\d+)?(?![\w.,])"
)

TAG = re.compile(r"<[^>]+>")
# Stylesheets and scripts are machinery, not assertions. Retro-validating the first
# build against an already-approved one-pager produced 16 findings, every one of them a
# CSS rgb() triple, font weight or width percentage — a gate that cries wolf gets
# switched off, and then there is no gate.
NOISE = re.compile(r"<(style|script)\b.*?</\1\s*>", re.S | re.I)
# A bare year is a date, not a figure claim. 1900-2100 only, so 2,500 is untouched.
YEAR = re.compile(r"^(19|20|21)\d\d$")


def prose_of(text):
    return TAG.sub(" ", NOISE.sub(" ", text))


def norm(s):
    """Compare figures by their digits and separators only, not their currency dressing."""
    return re.sub(r"[^\d.,%]", "", str(s)).strip(".,")


def figures_in(text):
    out = set()
    for m in FIGURE.finditer(text):
        n = norm(m.group())
        if n and not YEAR.match(n):
            out.add(n)
    return out


def read_line(path, line_no):
    p = pathlib.Path(path)
    if not p.exists():
        return None, "file does not exist: %s" % path
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return None, "unreadable: %s" % e
    if line_no is None:
        return text, None
    lines = text.splitlines()
    if not (1 <= int(line_no) <= len(lines)):
        return None, "line %s out of range (file has %d)" % (line_no, len(lines))
    return lines[int(line_no) - 1], None


def check(deliverable_text, claims):
    """Returns (verdict, findings). verdict in PASS / FAIL / INCONCLUSIVE."""
    findings = []
    if not claims:
        return "INCONCLUSIVE", ["no claims supplied — a verdict requires something that could fail"]

    sourced = set()
    for c in claims:
        fig = norm(c.get("figure", ""))
        if not fig:
            findings.append("claim with no figure: %r" % (c,))
            continue
        body, err = read_line(c.get("file"), c.get("line"))
        if err:
            findings.append("CANNOT READ  %s  -> %s" % (c.get("figure"), err))
            continue
        if fig in figures_in(body):
            sourced.add(fig)
        else:
            where = "%s:%s" % (c.get("file"), c.get("line") or "whole file")
            findings.append("FALSE CLAIM  %s  not present at %s" % (c.get("figure"), where))

    unreadable = [f for f in findings if f.startswith("CANNOT READ")]
    if unreadable and len(unreadable) == len(findings) and not sourced:
        return "INCONCLUSIVE", findings

    for fig in sorted(figures_in(prose_of(deliverable_text))):
        if fig not in sourced:
            findings.append("UNSOURCED    %s  appears in the deliverable with no claim" % fig)

    return ("PASS" if not findings else "FAIL"), findings


# ---------------------------------------------------------------- selftest
def selftest():
    import tempfile

    tmp = pathlib.Path(tempfile.mkdtemp())
    src = tmp / "source.md"
    src.write_text(
        "line one has nothing\n"
        "| Correos enviados | 44,131 |\n"
        "the rate was 97.5% sustained\n",
        encoding="utf-8",
    )
    cases, failed = 0, []

    def case(name, got, want):
        nonlocal cases
        cases += 1
        if got != want:
            failed.append("%s: got %s, want %s" % (name, got, want))

    # FAIL side — the defect this exists to catch
    v, f = check("El precio es S/ 8,670 al año.", [{"figure": "44,131", "file": str(src), "line": 2}])
    case("planted unsourced figure is caught", v, "FAIL")
    case("planted figure named", any("8,670" in x for x in f), True)

    v, _ = check("Correos: 44,131", [{"figure": "44,131", "file": str(src), "line": 3}])
    case("figure cited to the wrong line fails", v, "FAIL")

    v, _ = check("Correos: 44,131", [{"figure": "44,131", "file": str(tmp / "nope.md"), "line": 2}])
    case("missing source file is INCONCLUSIVE, not PASS", v, "INCONCLUSIVE")

    v, _ = check("Correos: 44,131", [{"figure": "44,131", "file": str(src), "line": 99}])
    case("out-of-range line is INCONCLUSIVE", v, "INCONCLUSIVE")

    v, _ = check("anything", [])
    case("zero claims cannot print PASS", v, "INCONCLUSIVE")

    # ALLOW side — weighted equally; a gate that reprobates clean work gets switched off
    v, f = check("Correos: 44,131", [{"figure": "44,131", "file": str(src), "line": 2}])
    case("true claim passes", v, "PASS")
    case("true claim is silent", f, [])

    v, _ = check(
        "44,131 correos y 97.5% de entregabilidad",
        [
            {"figure": "44,131", "file": str(src), "line": 2},
            {"figure": "97.5%", "file": str(src), "line": 3},
        ],
    )
    case("two true claims pass", v, "PASS")

    v, _ = check("S/ 44,131 en correos", [{"figure": "44,131", "file": str(src), "line": 2}])
    case("currency dressing does not break the match", v, "PASS")

    v, _ = check("Plan de carrera en 12 meses, 4 dias presencial.", [{"figure": "44,131", "file": str(src), "line": 2}])
    case("small prose integers are not treated as claims", v, "PASS")

    v, _ = check("<span>44,131</span> correos", [{"figure": "44,131", "file": str(src), "line": 2}])
    case("html tags do not hide or invent figures", v, "PASS")

    v, f = check(
        "<style>a{color:rgb(164,167,172);font-weight:600;width:46%}</style><p>44,131</p>",
        [{"figure": "44,131", "file": str(src), "line": 2}],
    )
    case("CSS is machinery, not claims", v, "PASS")

    v, _ = check("Vendedor #1 de 2023 a 2025. Correos: 44,131",
                 [{"figure": "44,131", "file": str(src), "line": 2}])
    case("a bare year is a date, not a figure claim", v, "PASS")

    v, _ = check("<style>.x{}</style>Cobró S/ 8,670 sin fuente.",
                 [{"figure": "44,131", "file": str(src), "line": 2}])
    case("stripping CSS does not blind the gate to a real unsourced figure", v, "FAIL")

    v, _ = check("Correos: 44,131", [{"figure": "44,131", "file": str(src)}])
    case("line may be omitted and still read the file", v, "PASS")

    print("selftest: %d/%d" % (cases - len(failed), cases))
    for x in failed:
        print("  FAIL  " + x)
    return 0 if not failed else 2


def main(argv):
    if "--selftest" in argv:
        return selftest()
    if len(argv) < 3:
        print(__doc__)
        return 2

    # The gate must prove it can still catch a planted defect before it judges real work.
    if selftest() != 0:
        print("PROBE FAILED — refusing to report on the real deliverable")
        return 2

    deliverable = pathlib.Path(argv[1])
    claims_path = pathlib.Path(argv[2])
    if not deliverable.exists():
        print("INCONCLUSIVE — deliverable not found: %s" % deliverable)
        return 2
    text = deliverable.read_text(encoding="utf-8", errors="replace")
    claims = json.loads(claims_path.read_text(encoding="utf-8"))

    verdict, findings = check(text, claims)
    print("\n%s — %s (%d claim(s))" % (verdict, deliverable.name, len(claims)))
    for f in findings:
        print("  " + f)
    return {"PASS": 0, "FAIL": 1, "INCONCLUSIVE": 2}[verdict]


if __name__ == "__main__":
    sys.exit(main(sys.argv))
