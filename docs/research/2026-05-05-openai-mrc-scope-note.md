# OpenAI MRC Scope Note

Source: OpenAI, "Supercomputer networking to accelerate large scale AI
training", May 5, 2026.

Link: https://openai.com/index/mrc-supercomputer-networking/

## Why It Matters

OpenAI's MRC post is strong evidence that frontier AI infrastructure failures
are not limited to individual GPUs. At large scale, network congestion, link
flaps, routing behavior, and failure absorption can determine whether a training
job keeps making progress.

For Ashiba's long-term framing, this supports a broader behavioral conformance
ontology:

- GPU/node conformance: local NVIDIA evidence, ECC, Xid, throttle, visibility.
- Fabric conformance: collective tail latency, path resilience, failure
  absorption, routing/failover regime.
- Workload conformance: whether the stack behaves as claimed under the relevant
  resource regime.

## Boundary For scanprobe

This does not justify adding fabric checks to `scanprobe` by default.

`scanprobe` remains the low-hanging-fruit local GPU evidence scan:

- read-only
- zero dependency
- no stress workload
- no NCCL/collective probe
- no fabric diagnosis
- no claim that a node or cluster is healthy

Fabric conformance is strategically adjacent, but it requires different
fixtures, different privileges, and different risk controls. Keep it out of the
default CLI until there is real hardware evidence and a safe, source-backed,
fixture-backed check that changes the user's next action.

## Product Implication

The honest default output should continue to say that `scanprobe` does not check
NCCL/fabric health. That caveat is a feature: it prevents local GPU evidence
from being mistaken for whole-cluster conformance.
