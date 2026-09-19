# fathom-foreman

A committed-state coherence read for [Foreman](https://github.com/thruwire/foreman),
built as an adapter over the hosted Fathom read.

Foreman supervises a Codex worker with TypeSafe's Jev. Jev answers the semantic
questions each step, progress, completeness, tests, stuck, off-track, and a
deterministic policy decides. Foreman keeps no committed-state record, and its
README flags false negatives, so the coherence axis, whether the worker
contradicted or forgot what the repository already holds, sits outside what the Jev
loop assesses. This adapter reads that axis on the same run.

## How it maps

The repository is the ground truth of what the worker committed, and the worker's
output lines are its claims about what it did.

- A file or symbol the run newly records becomes a commit. Recording the same one
  again lets the read raise `duplicate_commit`, which reads as redundant work.
- A worker output line that references a file or symbol becomes a claim. A reference
  to something the record never committed lets the read raise `stale_reference`,
  which reads as amnesia or confabulation.

Commits for a step precede that step's claims, so a worker may reference what it just
committed, and references resolve across steps, so citing a file made earlier stays
quiet.

## Install and run

```
pip install fathom-read
pip install -e .        # or drop foreman_fathom.py next to your code
python example_run.py --demo
```

`--demo` runs Foreman's own fake worker with no credentials and no model spend. The
worker claims to edit a file it never creates, and the read catches the omission on
the steps Foreman's assessment rated on-track. To read a real run instead:

```
python example_run.py --repo path/to/repo --run RUN_ID
```

Reads run free at a low anonymous limit, so no key is needed to try this. Pass
`--key` to use your own.

## The boundary

This package is the mapping. It turns a Foreman run into an op stream and calls the
public `fathom-read` client, which sends the ops to the hosted service and returns
the verdict. The read's computation, and the repair beyond it, stay in the service.
The extraction heuristics in `parse_diff` and `parse_output_refs` are the layer to
tune against real Codex output, and the op mapping does not change.

## Files

- `foreman_fathom.py`, the adapter, the op mapping, the read call, and the
  complementarity report.
- `example_run.py`, the demo and the reader for a persisted run.
- `test_foreman_fathom.py`, the offline test of the op mapping.

## Status

The op-mapping test passes offline with no network. The demo runs the full path
through Foreman's real runtime and the hosted read. On a real run, one gap remains,
Foreman does not yet persist the git diff on its observe events, so the adapter
pairs the events with a git snapshot, which the first real comparison wires up.
