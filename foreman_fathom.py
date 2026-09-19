"""foreman-fathom — a committed-state coherence read for Foreman (thruwire/foreman).

Foreman supervises a Codex worker with TypeSafe's Jev. Jev answers the semantic
questions each step, progress, completeness, tests, stuck, off-track, and a
deterministic policy decides. Foreman keeps no committed-state record, and its
README flags false negatives, so the coherence axis, whether the worker
contradicted or forgot what the repository already holds, sits outside what the
Jev loop assesses.

This adapter reads that axis on the same run. The repository is the ground truth of
what the worker committed, and the worker's output lines are its claims about what
it did. A claim that references a file or symbol the record never committed reads as
amnesia or confabulation. A file or symbol the record commits twice reads as
redundant work.

The read runs in the hosted Fathom service. This module maps a Foreman run to the
op stream and calls the public `fathom-read` client, which sends the ops to the
service and returns the verdict. It contains the mapping only. The read's
computation, and the repair beyond it, stay in the service.

Install: pip install fathom-read
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

import fathom_read as fr

# --- normalized run shape -----------------------------------------------------


@dataclass
class ForemanStep:
    """One iteration of a Foreman run, normalized to what the read needs.

    added_files and added_symbols carry what the repository newly recorded THIS
    step, as deltas, so a path or symbol that appears again later reads as a genuine
    redundant re-create rather than a file that simply stayed changed. worker_output
    carries the worker's own lines. assessment carries Foreman's Jev scores when
    known, so the two axes compare on the same step."""

    iteration: int
    worker_output: list[str] = field(default_factory=list)
    added_files: list[str] = field(default_factory=list)
    added_symbols: list[tuple[str, str]] = field(default_factory=list)  # (path, symbol)
    assessment: dict[str, float] | None = None


# --- extraction layer (tune against real runs; the read does not change) -------

_DIFF_NEWFILE = re.compile(r"^diff --git a/.+ b/(.+)$")
_ADDED_FILE = re.compile(r"^\+\+\+ b/(.+)$")
_NEWFILE_MARKER = re.compile(r"^new file mode")
_ADDED_SYM = re.compile(r"^\+\s*(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)")


def parse_diff(git_diff: str) -> tuple[list[str], list[tuple[str, str]]]:
    """Pull newly created files and newly added top-level symbols from a unified
    diff. New files come from the new-file marker, symbols from added def/class
    lines attributed to the file whose hunk they sit in."""
    new_files: list[str] = []
    symbols: list[tuple[str, str]] = []
    current = None
    pending_newfile = False
    for line in git_diff.splitlines():
        m = _DIFF_NEWFILE.match(line)
        if m:
            current, pending_newfile = m.group(1), False
            continue
        if _NEWFILE_MARKER.match(line):
            pending_newfile = True
            continue
        mf = _ADDED_FILE.match(line)
        if mf:
            current = mf.group(1)
            if pending_newfile and current not in new_files:
                new_files.append(current)
            pending_newfile = False
            continue
        ms = _ADDED_SYM.match(line)
        if ms and current is not None:
            symbols.append((current, ms.group(1)))
    return new_files, symbols


_TEXT_PATH = re.compile(r"([A-Za-z0-9_./-]+\.py)")
# trailing (?![\w.]) keeps this off the leading token of a filename, so
# "import db.py" reads as a file reference rather than a symbol reference to "db".
_USES_SYM = re.compile(r"\b(?:import|from|calls?|uses?|invoke[sd]?)\s+([A-Za-z_]\w*)(?![\w.])")


def _refs_from_json_line(obj: dict[str, Any]) -> list[tuple[str, str]]:
    refs: list[tuple[str, str]] = []
    blob = json.dumps(obj)
    for path in set(_TEXT_PATH.findall(blob)):
        refs.append(("file", path))
    for key in ("path", "file", "filename", "target"):
        val = obj.get(key)
        if isinstance(val, str) and val.endswith(".py"):
            refs.append(("file", val))
    return refs


def parse_output_refs(lines: Iterable[str]) -> list[tuple[str, str]]:
    """Return the files and symbols the worker's output claims to use or have made.
    Conservative on purpose. A missed claim costs a finding we do not raise, while a
    wrong claim would raise a false stale_reference, so this prefers to miss."""
    refs: list[tuple[str, str]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        parsed = None
        if line.startswith("{"):
            try:
                parsed = json.loads(line)
            except ValueError:
                parsed = None
        if isinstance(parsed, dict):
            refs.extend(_refs_from_json_line(parsed))
            continue
        for path in set(_TEXT_PATH.findall(line)):
            refs.append(("file", path))
        for sym in set(_USES_SYM.findall(line)):
            refs.append(("symbol", sym))
    seen: set[tuple[str, str]] = set()
    out: list[tuple[str, str]] = []
    for r in refs:
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


# --- op mapping ---------------------------------------------------------------


def steps_to_ops(steps: list[ForemanStep]) -> list[fr.Op]:
    """Walk a run in order and build the committed-state op stream.

    A newly recorded file or symbol becomes an add, so a second recording of the
    same one lets the read raise duplicate_commit. A worker output reference becomes
    an answer carrying that reference, so a reference to something the record never
    committed lets the read raise stale_reference. Commits for a step precede that
    step's claims, since a worker may reference what it just committed. A symbol keys
    on its bare name so a reference by name resolves, and carries its file as the
    value so a redefinition in the same file reads as duplicate."""
    ops: list[fr.Op] = []
    step_no = 0
    for step in steps:
        step_no += 1
        for path in step.added_files:
            ops.append(fr.Op(op="add", kind="file", key=path, value="create", step=step_no))
        for path, sym in step.added_symbols:
            ops.append(fr.Op(op="add", kind="symbol", key=sym, value=path, step=step_no))
        for i, ref in enumerate(parse_output_refs(step.worker_output)):
            ops.append(fr.Op(op="answer", kind="claim", key=f"s{step_no}-c{i}",
                             refs=[ref], step=step_no))
    return ops


# --- read + comparison --------------------------------------------------------


def read_run(steps: list[ForemanStep], key: str | None = None) -> fr.Verdict:
    """Send the run's op stream to the hosted read and return its verdict."""
    return fr.read(steps_to_ops(steps), key=key)


def foreman_flagged(assessment: dict[str, float] | None, thr: float = 0.5) -> bool:
    """Foreman's nearest signal that something is wrong on a step. Jev has no
    committed-state field, so off-track and stuck are the closest it computes."""
    if not assessment:
        return False
    return (assessment.get("work_off_track", 0.0) >= thr
            or assessment.get("worker_stuck", 0.0) >= thr)


def complementarity(steps: list[ForemanStep], thr: float = 0.5, key: str | None = None) -> dict[str, Any]:
    """The headline. Count the coherence findings the read raises on steps where
    Foreman's own assessment stayed calm, meaning the read caught a committed-state
    break the semantic loop passed over."""
    verdict = read_run(steps, key=key)
    findings = [{"kind": f.kind, "key": f.key, "step": f.step, "detail": f.detail}
                for f in verdict.findings]
    flagged = {s.iteration for s in steps if foreman_flagged(s.assessment, thr)}
    step_iter = {i + 1: s.iteration for i, s in enumerate(steps)}
    missed = [f for f in findings if step_iter.get(f["step"]) not in flagged]
    return {
        "coherent": verdict.coherent,
        "total_findings": len(findings),
        "caught_by_read_missed_by_foreman": len(missed),
        "missed_detail": missed,
        "findings": findings,
    }


# --- real-run extractor (best effort; validated in the joint run) --------------


def events_to_steps(events: list[dict[str, Any]]) -> list[ForemanStep]:
    """Group persisted Foreman FactoryEvent dicts into normalized steps. WORKER_OUTPUT
    lines become worker_output, an observe or repository-change payload's git_diff
    becomes the step's added files and symbols, and an assessment payload becomes the
    step's scores. Steps break on each FOREMAN_ASSESSED, which marks one loop
    iteration. Foreman does not yet persist the git diff on its observe events, so a
    real run pairs these events with a git snapshot, which the joint run wires up."""
    steps: list[ForemanStep] = []
    cur = ForemanStep(iteration=0)
    seen_files: set[str] = set()
    seen_syms: set[tuple[str, str]] = set()
    for ev in events:
        etype = ev.get("event_type")
        payload = ev.get("payload", {}) or {}
        if etype == "WORKER_OUTPUT":
            line = payload.get("line")
            if isinstance(line, str):
                cur.worker_output.append(line)
        elif etype in ("REPOSITORY_CHANGED", "FOREMAN_OBSERVED"):
            diff = payload.get("git_diff") or (payload.get("observation") or {}).get("git_diff", "")
            if diff:
                nf, syms = parse_diff(diff)
                cur.added_files.extend(p for p in nf if p not in seen_files)
                seen_files.update(nf)
                cur.added_symbols.extend(s for s in syms if s not in seen_syms)
                seen_syms.update(syms)
        elif etype == "FOREMAN_ASSESSED":
            cur.assessment = payload.get("assessment") or {
                k: v for k, v in payload.items() if isinstance(v, (int, float))
            }
            steps.append(cur)
            cur = ForemanStep(iteration=cur.iteration + 1)
    if cur.worker_output or cur.added_files or cur.added_symbols:
        steps.append(cur)
    return steps
