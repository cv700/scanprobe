# GPU Capacity Acceptance Receipt

`gpu_capacity_delivery_match_v0` is a narrow synthetic claim pack for one question:

> We paid for a specific GPU capacity promise. Did the delivered machine match it,
> and is there enough bound evidence to accept it?

This is not generic monitoring. Monitoring watches a machine after it exists.
This receipt binds separate evidence into a capacity acceptance decision.

## Chain

```text
order -> allocation -> machine evidence -> health evidence -> probe manifest -> receipt -> accept/pay/dispute/route
```

## Evidence

`order` is the buyer/provider promise: reservation or order ID, GPU count, GPU
SKU/class, region/zone, and delivery window.

`allocation` is the provider-side assignment: allocation ID, machine ID, count,
SKU/class, and region/zone.

`machine_evidence` is what the machine says it is. In the demo packet this is a
synthetic `nvidia_smi` capture with GPU count, SKU/class, region/zone, driver,
and GPU identities.

`health_evidence` is whether the delivered capacity passed a declared diagnostic.
The demo uses DCGM, but the rule deliberately checks the declared diagnostic
level and result instead of requiring DCGM Level 3 everywhere.

`probe_manifest` is the binding artifact. It ties the evidence bundle back to
the reservation, allocation, and machine. Missing manifest means `UNKNOWN`.
A manifest bound to the wrong reservation, allocation, or machine means
`CONTRADICTED`.

## Verdicts

`SUPPORTED` means every deterministic pass supports the capacity delivery claim.

`UNKNOWN` means evidence is missing. Missing evidence does not prove the provider
failed to deliver; it means the receipt is incomplete.

`CONTRADICTED` means available evidence conflicts with the order or binding.
Wrong GPU count, SKU/class, region/zone, failed DCGM, or wrong manifest binding
are contradictions.

## Demo

```bash
python -m ashiba_scanprobe.claims demo_packets/gpu_capacity_delivery_match_unknown.json
python -m ashiba_scanprobe.claims demo_packets/gpu_capacity_delivery_match_supported.json
python -m ashiba_scanprobe.claims demo_packets/gpu_capacity_delivery_match_contradicted.json
```

The three demo packets are synthetic. They do not call provider APIs, rent GPUs,
or require real hardware. The point is to make the acceptance receipt shape
obvious before connecting it to real packet capture.
