# -*- coding: utf-8 -*-
"""send_gate: an LLM-written outbound draft may leave the machine only with a PASS from every gate.

    python send_gate.py <draft.md> <claims.json>

  1. plan     declares the stages: slop, claims
  2. slop     gates/prose_slop.py   machine-slop vocabulary and register (evidence-based lexicon)
  3. claims   gates/claim_check.py  every figure in the draft is re-read at its cited source line
  4. require  gates/receipts.py     refuses unless both passed on the exact bytes on disk now

Each gate runs as its own process and is judged by its exit code, never by its own summary,
so the checker and the thing it checks never share an author.
Exit 0 = cleared to send. Exit 1 = refused, with the reason.
"""
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE / "gates"))
import receipts  # noqa: E402


def run(script, *args):
    p = subprocess.run([sys.executable, str(HERE / "gates" / script), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.returncode, (p.stdout + p.stderr).strip()


def main(draft, claims):
    receipts.plan(draft, ["slop", "claims"])
    code, out = run("prose_slop.py", draft)
    receipts.record(draft, "slop", code == 0, out.splitlines()[-1] if out else "")
    print(f"[slop]   {'PASS' if code == 0 else 'FAIL'}\n" + "\n".join("         " + l for l in out.splitlines()[-6:]))
    code, out = run("claim_check.py", draft, claims)
    receipts.record(draft, "claims", code == 0, out.splitlines()[-1] if out else "")
    print(f"[claims] {'PASS' if code == 0 else 'FAIL'}\n" + "\n".join("         " + l for l in out.splitlines()[-6:]))
    try:
        receipts.require(draft)
    except receipts.Refused as e:
        print(f"REFUSED  {e}")
        return 1
    print("CLEARED  every declared gate passed on these exact bytes")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
