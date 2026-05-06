# Contributing

You do not need a GPU.

```bash
python3 tests/test_scoring.py
python3 tests/test_nvidia_smi_parsing.py
python3 tests/test_xid_parsing.py
python3 tests/test_edge_cases.py
```

If you do have a GPU, run `python3 scanprobe.py --json` and include the output
in the issue or PR.

Small, verified fixes are better than new scope.

Above everything: do no harm. New checks must be read-only unless there is an
explicit, reviewed reason to do otherwise.
