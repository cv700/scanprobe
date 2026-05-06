# Xid Classification Note

This note records the current `scanprobe` Xid policy. Xid classification is
high-risk correctness work because a bad bucket can either scare an operator away
from a usable node or understate evidence that deserves draining.

## Sources

- NVIDIA Xid Errors, Working with Xid Errors:
  https://docs.nvidia.com/deploy/xid-errors/working-with-xid-errors.html
- NVIDIA Xid Errors, Xid Catalog:
  https://docs.nvidia.com/deploy/xid-errors/analyzing-xid-catalog.html
- NVIDIA Dynamic Page Retirement, XID Reporting:
  https://docs.nvidia.com/deploy/topics/topic_4_1.html

## Policy

`scanprobe` uses conservative local triage language:

- `DRAIN` means visible local evidence suggests the node should not receive new
  work until the evidence is resolved.
- `WATCH` means visible local evidence deserves inspection and correlation
  before rerunning long or expensive work.
- `INFO` Xids remain in JSON event details but do not change the node tier.

For Xids with an NVIDIA immediate action of `RESET_GPU` or a reset/restart style
workflow, `scanprobe` uses `DRAIN`. This is not an automatic reset instruction.
It is a scheduling recommendation: do not launch new work on the node until the
operator/provider resolves the reset-class evidence.

For Xid 154, `scanprobe` does not treat the Xid number alone as drain-class.
It parses the recovery action text and escalates only if the visible action says
drain, reset, or reboot.

## Current Buckets

Drain-class:

- 46: GPU stopped processing; NVIDIA catalog immediate action is reset-class.
- 48: double-bit ECC error; NVIDIA notes reset or node reboot can be needed.
- 62: internal micro-controller halt; reset-class.
- 64: memory remapping / retirement failure; reset-class.
- 74: NVLink error; handled as drain-class because fabric/link failures should
  not receive new work before investigation.
- 79: GPU has fallen off the bus; restart-bare-metal class.
- 95: uncontained error; reset-class.
- 109: context switch timeout; reset-class.
- 110: security fault; reset/cold-reset style recovery.
- 119: GSP RPC timeout; reset-class if persistent.
- 120: GSP error; reset-class if persistent.
- 136: link training failed; reset-class.
- 140: unrecovered ECC error; reset-class.
- 143: GPU initialization error; kept drain-class pending real fixtures.
- 155: NVLink software-defined error; reset-class, though the catalog notes some
  intentional link transitions can trigger it.
- 156: resource retirement event; reset-class.
- 158: GPU fatal timeout; reset-class.

Watch-class:

- 13, 31, 32, 43, 45, 69: application, channel, or command-stream faults that
  often point first to app/CUDA correlation, but can still matter during incident
  triage.
- 63: memory remapping event; NVIDIA describes it as handled, but it remains
  visible memory-maintenance evidence worth correlating if the node acted weird.
- 92, 94: ECC/channel conditions that are contained or investigatory.
- 137, 157, 160, 161: link/resource/channel retirement or privilege events that
  should be correlated before long reruns.

## Known Weaknesses

- These buckets are source-backed but not yet fixture-backed across real
  incidents.
- Some Xids are context-sensitive. A solo application-fault Xid may not indicate
  node risk, while the same Xid next to ECC or fabric events may be important.
- `scanprobe` does not yet display `INFO` Xids in human output. JSON keeps parsed
  event details for users who need the full local evidence.
- NVIDIA catalog guidance can vary by GPU generation, driver branch, and whether
  an event is solo or accompanied by other errors. Real fixtures should drive
  future tightening.
