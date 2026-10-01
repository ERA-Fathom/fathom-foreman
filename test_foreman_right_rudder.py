"""Offline test for the right-rudder-foreman op mapping.

This validates the adapter, that a Foreman run maps to the right committed-state op
stream, with no network and no read logic in the repo. The read itself runs in the
hosted Right Rudder service and is exercised by example_run.py. Here we check that the ops
encode the conditions the read looks for, a reference to something never committed,
a re-recorded file or symbol, and clean references that resolve.

Run: python test_foreman_right_rudder.py
"""

from __future__ import annotations

from foreman_right_rudder import ForemanStep, steps_to_ops


def _adds(ops):
    return [(o.kind, o.key, o.value) for o in ops if o.op == "add"]


def _refs_with_prior_add(ops):
    """Return (resolved, unresolved) references, where resolved means an add of the
    same (kind, key) appears earlier in the stream. This is plain set logic used to
    check the op stream, not the read's computation."""
    committed = set()
    resolved, unresolved = [], []
    for o in ops:
        if o.op == "add":
            committed.add((o.kind, o.key))
        for ref in o.refs:
            (resolved if tuple(ref) in committed else unresolved).append(tuple(ref))
    return resolved, unresolved


def main() -> int:
    ok = True

    def check(name, cond, detail=""):
        nonlocal ok
        ok = ok and cond
        print(f"  {'PASS' if cond else 'FAIL'}  {name}{('  ' + detail) if detail else ''}")

    print("=== right-rudder-foreman op-mapping test (offline) ===")

    # A. CLEAN. A reference to a file committed the same step resolves.
    clean = steps_to_ops([ForemanStep(
        iteration=0, added_files=["app.py"], added_symbols=[("app.py", "handler")],
        worker_output=["editing app.py"])])
    _, unresolved = _refs_with_prior_add(clean)
    check("clean run leaves no unresolved reference", unresolved == [], f"unresolved={unresolved}")

    # B. AMNESIA. A reference to a file the record never adds stays unresolved.
    amnesia = steps_to_ops([ForemanStep(
        iteration=0, added_files=["app.py"], worker_output=["editing cache.py"])])
    _, unresolved = _refs_with_prior_add(amnesia)
    check("unrecorded reference is unresolved", ("file", "cache.py") in unresolved,
          f"unresolved={unresolved}")

    # C. REDUNDANT WORK. The same file and symbol added twice.
    dup = steps_to_ops([
        ForemanStep(iteration=0, added_files=["auth.py"], added_symbols=[("auth.py", "login")]),
        ForemanStep(iteration=1, added_files=["auth.py"], added_symbols=[("auth.py", "login")]),
    ])
    adds = _adds(dup)
    check("re-created file appears as a repeated add",
          adds.count(("file", "auth.py", "create")) == 2, f"adds={adds}")
    check("re-defined symbol appears as a repeated add",
          adds.count(("symbol", "login", "auth.py")) == 2, f"adds={adds}")

    # D. LEGIT CROSS-STEP REFERENCE. A reference to an earlier commit resolves.
    legit = steps_to_ops([
        ForemanStep(iteration=0, added_files=["db.py"]),
        ForemanStep(iteration=1, worker_output=["import db.py", "uses db.py"]),
    ])
    _, unresolved = _refs_with_prior_add(legit)
    check("reference to an earlier commit resolves", unresolved == [], f"unresolved={unresolved}")

    # E. ORDER. Commits precede claims within a step, so a same-step reference resolves.
    same = steps_to_ops([ForemanStep(
        iteration=0, added_files=["x.py"], worker_output=["uses x.py"])])
    kinds = [o.op for o in same]
    check("adds precede answers within a step", kinds == sorted(kinds, key=lambda k: k != "add"),
          f"ops={kinds}")

    print(f"\n  {'ALL PASS' if ok else 'FAILURES ABOVE'}")
    print("  the read runs in the hosted service; example_run.py exercises it end to end.")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
