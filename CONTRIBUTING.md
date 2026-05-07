# Contributing to scanprobe

You don't need a GPU.

```bash
git clone https://github.com/cv700/scanprobe
cd scanprobe
pip install -e .
python tests/test_scoring.py
python tests/test_nvidia_smi_parsing.py
```

Both test files run without hardware. All 56 tests must pass before opening a PR.

**If you have a GPU**, paste your `scanprobe --json` output in the PR. That output
is the most valuable thing you can contribute right now — the code has not yet been
validated on real hardware.

**Found a wrong Xid code or wrong threshold?** Open an issue with your dmesg output
or nvidia-smi output. Those fixes are high priority.

**Found that the tool was useful?** That's also worth a note. The goal is a tool
that serious GPU infrastructure researchers trust enough to keep around.

MIT license. All contributions welcome.
