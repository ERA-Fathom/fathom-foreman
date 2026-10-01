"""example_run.py — run the coherence read over a Foreman run.

Two modes.

  python example_run.py --demo
      Run Foreman's built-in fake worker with no credentials and no model spend,
      where the worker claims to edit a file it never creates, then read the run and
      print the coherence findings beside Foreman's own assessment.

  python example_run.py --repo PATH --run RUN_ID
      Read a persisted Foreman run from PATH/.foreman/runs/RUN_ID and print the same.

Both modes send the op stream to the hosted Right Rudder read through the public
`right-rudder` client. Reads run free at a low anonymous limit, so no key is needed
to try this. Pass --key to use your own.
"""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

import foreman_right_rudder as ff


def _print(comp: dict) -> None:
    print(f"coherent={comp['coherent']}  findings={comp['total_findings']}  "
          f"caught_by_read_missed_by_foreman={comp['caught_by_read_missed_by_foreman']}")
    for f in comp["missed_detail"]:
        print(f"  read caught, Foreman calm:  {f['kind']}  {f['key']}  (step {f['step']})")


def _load_events(repo: Path, run_id: str) -> list[dict]:
    path = repo / ".foreman" / "runs" / run_id / "events.jsonl"
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_demo(key: str | None) -> int:
    """A zero-spend demo through Foreman's real runtime with a planted omission."""
    from foreman.config import FactoryConfig
    from foreman.foreman import FakeForemanModel
    from foreman.models import WorkerType
    from foreman.runtime import FactoryRuntime
    from foreman.workers import FakeWorker

    repo = Path("./_foreman_demo_repo").resolve()
    repo.mkdir(exist_ok=True)

    def worker_factory(worker_type):
        if worker_type is WorkerType.VERIFIER:
            return FakeWorker(output_lines=["Verifying requirements and tests"], delay_seconds=0.02)
        return FakeWorker(
            output_lines=["Implementing the change", "editing cache.py", "import cache.py"],
            delay_seconds=0.05,
        )

    config = FactoryConfig(
        assessment_min_interval_seconds=0.04, periodic_assessment_seconds=0.25,
        worker_timeout_seconds=5.0, overall_timeout_seconds=10.0,
        max_workers=2, max_retries=1, max_iterations=8,
    )
    runtime = FactoryRuntime(repository=repo, job="Add a cache layer",
                             model=FakeForemanModel(), config=config, worker_factory=worker_factory)
    asyncio.run(runtime.run())
    run_id = runtime.state.run_id
    print(f"demo run {run_id} (planted omission: worker cites cache.py, never creates it)")
    steps = ff.events_to_steps(_load_events(repo, run_id))
    _print(ff.complementarity(steps, key=key))
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="run a zero-spend planted demo")
    ap.add_argument("--repo", type=Path, help="repository holding a persisted run")
    ap.add_argument("--run", help="run id under REPO/.foreman/runs")
    ap.add_argument("--key", default=None, help="optional Right Rudder key; reads run free without one")
    a = ap.parse_args(argv)

    if a.demo:
        return run_demo(a.key)
    if a.repo and a.run:
        steps = ff.events_to_steps(_load_events(a.repo, a.run))
        _print(ff.complementarity(steps, key=a.key))
        return 0
    ap.error("pass --demo, or --repo and --run")


if __name__ == "__main__":
    raise SystemExit(main())
