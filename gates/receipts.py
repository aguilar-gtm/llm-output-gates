# -*- coding: utf-8 -*-
"""receipts: make skipping a gate impossible, not just discouraged.

A gate that exists but isn't called is worth nothing. The mechanism:

  1. A build DECLARES its stages up front:   plan(artifact, ["slop", "claims"])
  2. Each gate writes a receipt bound to the artifact's sha256:   record(artifact, "slop", passed)
  3. The send step calls require(artifact), which refuses unless EVERY declared stage
     has a PASS receipt whose hash still matches the file on disk right now.

That leaves three ways to reach a send, and all three are honest:
  - run every gate and pass   -> ship
  - skip a gate               -> refused: "no receipt for <stage>"
  - edit after passing        -> refused: "receipt is stale"
Never declaring a plan isn't a loophole: require() with no plan is itself a refusal.

Generic re-implementation of the receipts ledger from a private production engine, where
it gates every deliverable before it leaves the machine.

    python receipts.py --test
"""
import hashlib
import json
import os
import sys
import tempfile
import time
from pathlib import Path

LEDGER = Path(os.environ.get("RECEIPTS_DIR", Path.cwd() / ".receipts"))


class Refused(Exception):
    """The artifact may not ship. The message says which stage and why."""


def sha256(path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _file(artifact) -> Path:
    key = hashlib.sha256(str(Path(artifact).resolve()).encode("utf-8")).hexdigest()[:16]
    return LEDGER / f"{key}.json"


def _load(artifact) -> dict:
    p = _file(artifact)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _save(artifact, data):
    LEDGER.mkdir(parents=True, exist_ok=True)
    tmp = _file(artifact).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, _file(artifact))  # atomic: a crash never leaves half a ledger


def plan(artifact, stages):
    """Declare what 'verified' means for this artifact before any gate runs."""
    stages = list(dict.fromkeys(stages))
    if not stages:
        raise ValueError("a plan with no stages verifies nothing")
    d = _load(artifact)
    d.update({"artifact": str(artifact), "plan": stages, "planned_at": time.time()})
    d.setdefault("receipts", {})
    _save(artifact, d)


def record(artifact, stage, passed, detail=""):
    """A gate's verdict, bound to the exact bytes it judged."""
    d = _load(artifact)
    d.setdefault("receipts", {})[stage] = {
        "verdict": "PASS" if passed else "FAIL", "sha256": sha256(artifact),
        "at": time.time(), "detail": str(detail)[:500]}
    _save(artifact, d)


def require(artifact):
    """Raise Refused unless every declared stage passed on the bytes on disk now."""
    d = _load(artifact)
    if not d.get("plan"):
        raise Refused(f"{artifact}: no plan declared. An unplanned build is an unverified build.")
    now = sha256(artifact)
    for stage in d["plan"]:
        r = d["receipts"].get(stage)
        if r is None:
            raise Refused(f"{artifact}: no receipt for '{stage}' (the gate never ran)")
        if r["verdict"] != "PASS":
            raise Refused(f"{artifact}: '{stage}' FAILED: {r['detail']}")
        if r["sha256"] != now:
            raise Refused(f"{artifact}: receipt for '{stage}' is stale (the file changed after it passed)")
    return True


# ---------------------------------------------------------------- adversarial suite
def _test() -> int:
    global LEDGER
    fails, n = 0, 0

    def case(name, fn, want):
        nonlocal fails, n
        n += 1
        try:
            fn()
            got = "ok"
        except Refused as e:
            got = "refused:" + str(e).split(": ", 1)[-1][:40]
        except ValueError:
            got = "valueerror"
        ok = got.startswith(want)
        fails += not ok
        print(("PASS " if ok else "FAIL ") + f"{name} -> {got}")

    with tempfile.TemporaryDirectory() as tmp:
        LEDGER = Path(tmp) / "ledger"
        a = Path(tmp) / "email.txt"
        a.write_text("Hi Ana, saw the Series A.", encoding="utf-8")
        case("no plan is a refusal, not a loophole", lambda: require(a), "refused:no plan")
        case("empty plan is rejected", lambda: plan(a, []), "valueerror")
        plan(a, ["slop", "claims"])
        case("planned, nothing ran", lambda: require(a), "refused:no receipt for 'slop'")
        record(a, "slop", True)
        case("one of two stages ran", lambda: require(a), "refused:no receipt for 'claims'")
        record(a, "claims", False, "figure 40% unsourced")
        case("a FAIL receipt blocks", lambda: require(a), "refused:'claims' FAILED")
        record(a, "claims", True)
        case("every stage passed", lambda: require(a), "ok")
        a.write_text("Hi Ana, saw the Series A. We 10x pipeline.", encoding="utf-8")
        case("edit after passing = stale", lambda: require(a), "refused:receipt for 'slop' is stale")
        record(a, "slop", True)
        record(a, "claims", True)
        case("re-run gates on the new bytes", lambda: require(a), "ok")
        b = Path(tmp) / "other.txt"
        b.write_text("Hi Ana, saw the Series A. We 10x pipeline.", encoding="utf-8")
        case("same bytes, different file: no borrowed receipts", lambda: require(b), "refused:no plan")
    print(f"receipts selftest: {n - fails}/{n}")
    return 1 if fails else 0


if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(_test())
    print(__doc__)
