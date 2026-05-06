---
name: New signal or feature request
about: Propose a new read-only check, signal, or output change
title: "[feature] "
labels: feature, needs-source, needs-fixture
---

## Proposed change

What should `scanprobe` add or change?

## User moment

When would the user run this?

- before rerun
- after job failure
- before draining
- before filing support ticket
- other:

## Next action changed

What decision would this change?

- rerun here
- inspect
- drain/exclude node
- file provider/admin support ticket
- other:

## Safety

The default scan must remain read-only.

- Does this require `sudo`? yes/no
- Does this write files outside explicit fixture collection? yes/no
- Does this mutate GPU, driver, clocks, persistence mode, scheduler, kernel, or
  monitoring state? yes/no
- Does this run an active workload, stress test, benchmark, NCCL collective, or
  DCGM diagnostic? yes/no
- Does this contact any network service? yes/no

## Sources

Link official docs, vendor docs, issue reports, or public incident reports that
show this is common in real troubleshooting.

## Fixtures

Attach or describe redacted real output that should become a fixture.

If there is no source and no fixture, this probably does not belong in the
default scan yet.
