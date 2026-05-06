# Contributing

You do not need a GPU.

```bash
python3 tests/test_scanprobe.py
python3 scanprobe.py --help
```

If you do have a GPU, run `python3 scanprobe.py --json` and include the output
in the issue or PR.

For deeper validation, use `bash scripts/collect-fixture.sh`. It writes to
`local-fixtures/`, which is ignored by git. Redact before sharing anything.

Small, verified fixes are better than new scope.

Above everything: do no harm. New checks must be read-only unless there is an
explicit, reviewed reason to do otherwise.
