# Contributing

You do not need a GPU.

```bash
python3 tests/test_scanprobe.py
python3 scanprobe.py --help
```

If you do have a GPU, run both commands and include the redacted output in the
issue or PR:

```bash
python3 scanprobe.py
python3 scanprobe.py --json
```

For verdict or parsing reports, include:

- redacted `python3 scanprobe.py` output
- redacted `python3 scanprobe.py --json` output
- scanprobe commit or version
- verdict: `CLEAR`, `WATCH`, `DRAIN`, or `UNKNOWN`
- primary issue line
- GPU model and driver version
- whether the command ran on host, container, Kubernetes, Slurm, notebook, or
  another environment
- whether kernel logs were visible from that shell
- whether MIG or vGPU was enabled
- what you would have done before running `scanprobe`
- what you would do after seeing the output
- what visible local evidence would have changed your action but was missing

For deeper validation, use `bash scripts/collect-fixture.sh`. It writes to
`local-fixtures/`, which is ignored by git. Redact before sharing anything.

Small, verified fixes are better than new scope.

Above everything: do no harm. New checks must be read-only unless there is an
explicit, reviewed reason to do otherwise.

New default signals need all five:

- read-only
- source-backed
- fixture-backed
- common in real reports
- able to change the user's next action
