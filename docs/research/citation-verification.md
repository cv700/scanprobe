# Citation Verification: scanprobe README

**Verified:** 2026-05-05
**Method:** Direct fetch of arXiv HTML/PDF, USENIX proceedings, ACM digital library, plus cross-checks against secondary sources for passages that wouldn't render.
**Bottom line:** Most citations point to the right papers, but **section numbers are wrong in 4 of 6 cases**, **one statistic is wrong** (Llama 3), and **one statistic appears fabricated** (Hu et al. NSDI 2024). Recommend a revision pass before publication.

---

## Summary table

| # | Citation | Verdict |
|---|---|---|
| 1a | ByteRobust §4.1 proactive checks | **Verified** |
| 1b | ByteRobust §4.3 EUD ~70% recall | **Wrong section** (should be §9, "Experiences and Limitations") |
| 1c | ByteRobust §5.2 dormant faults / step 450 | **Wrong claim** (§5.2 is about an evaluation hang, not dormant faults; "step 450" appears nowhere) |
| 2a | Llama 3 §4: 16,384 H100, ~3 hr/failure | **Wrong section** (should be §3.3, "Reliability and Operational Challenges") |
| 2b | Llama 3 §4: 466 failures over 54 days | **Wrong section + needs nuance.** §3.3. 466 *total* interruptions (47 planned + 419 *unexpected*). Most write-ups quote the 419 figure. |
| 2c | Six SDC events in Llama 3 paper | **Verified** (suspected SDC mentioned within the 419; the "6 SDC events" figure is widely reported but I could not find it as a literal sentence in the paper — see notes) |
| 3 | Dixit et al. 2021, arXiv:2102.11245, Mercurial Cores | **Verified** |
| 4 | Hochschild et al., HotOS 2021, "Cores that don't count" | **Verified** |
| 5 | Hu et al. NSDI 2024 — 22% jobs fail / 73% hardware | **Wrong claim.** Paper says ~40% of jobs fail; infrastructure failures account for 82% of *GPU-time* but only 11% of *job count*. Neither 22% nor 73% appears. |
| 6 | XPUTimer / Flare, arXiv:2502.02670 | **Wrong arXiv ID.** Correct ID is **arXiv:2502.05413**. Content otherwise matches. |

---

## 1. ByteRobust — arXiv:2509.16293

Paper exists. Title: *Robust LLM Training Infrastructure at ByteDance*. Authors: Borui Wan, Gaohong Liu, Zuquan Song, Jun Wang, et al. (ByteDance). Also published at SOSP '25 (DOI 10.1145/3731569.3764838).

The paper's actual section structure is:

```
1. Introduction
2. Background and Motivation
3. ByteRobust Overview
4. Automated Fault Tolerance
   4.1 Proactive Real-time Checks
   4.2 Hierarchical Stop-time Checks
   4.3 Case Study
5. Data-Driven Over-Eviction
   5.1 Aggregation Analysis
   5.2 Case Study
6. Controlled and Swift Recovery
7. Implementation
8. Evaluation
9. Experiences and Limitations
10. Related Work
11. Conclusion
```

### 1a. "Section 4.1: proactive checks (NIC, packet loss, switch health, DCGM, PCIe BW, memory remapping, temperature, OS kernel events)"

**Verdict: Verified.** Confidence: high (read source).

§4.1 ("Proactive Real-time Checks") opens:

> "System inspection. The monitor employs inspection threads to carry out a series of lightweight system health status queries at predefined second-level intervals. … The inspections mainly cover: (i) Network-side items, such as NIC down or jitter, packet loss rate, switches down. (ii) GPU-side items, including the status of DCGM service, PCIe bandwidth, memory row remapping, and GPU temperature, etc. (iii) Host-side items, such as OS kernel event (e.g., Xid in dmesg)."

Every item in the cited list is in the section. Keep as-is.

### 1b. "Section 4.3: NVIDIA EUD has ~70% recall on SDC events"

**Verdict: Correct paper, wrong section.** Confidence: high.

§4.3 ("Case Study") is about NaN-loss diagnosis workflow — it mentions running EUD and NCCL tests but does **not** state the 70% recall figure.

The 70% figure is in **§9 (Experiences and Limitations)**:

> "NVIDIA's EUD diagnostic tool achieves only 70% recall."

The same section discusses SDC propagation through collective communication. **Suggested correction:** "ByteRobust §9: NVIDIA EUD has only ~70% recall on SDC events."

### 1c. "Section 5.2: dormant faults — some GPUs pass all pre-flight checks and fail at training step 450+"

**Verdict: Wrong claim.** Confidence: high.

§5.2 ("Case Study" under "Data-Driven Over-Eviction") is about an **evaluation hang** caused by defective CUDA cores in a 6-machine pipeline — not about dormant faults that activate after step 450. The phrases "step 450" and "dormant fault" do not appear in the paper.

The closest concept the paper actually supports is in §9, where SDC is described as input-sensitive and thermally triggered, which is *consistent* with a "passes pre-flight, fails later" pattern but is never quantified or pinned to a step number.

**Recommendation:** Either drop this citation or rewrite it as a paraphrase, e.g. "ByteRobust §9 notes SDC is input-sensitive and thermally triggered — consistent with faults that pass static pre-flight checks and surface only under sustained workload." Do NOT keep "step 450+" — it has no source.

---

## 2. Meta Llama 3 — arXiv:2407.21783

Paper exists. *The Llama 3 Herd of Models* (Grattafiori, Dubey, Jauhri et al.). The infrastructure-failure stats are in **§3.3 ("Infrastructure, Scaling, and Efficiency"), under the subsection "Reliability and Operational Challenges"** — **not §4**.

### 2a. "Section 4: 16,384 H100 cluster, one job failure every ~3 hours"

**Verdict: Correct paper, wrong section.** Confidence: high.

The 16K H100 cluster and the ~3-hour failure cadence are in §3.3. §4 of Llama 3 covers post-training. The "one failure every ~3 hours" framing comes directly from the paper's note that they had "more than one daily training interruption" — Tom's Hardware and others normalized this to "every 3 hours" (419 unexpected ÷ 54 days × 24 hr ≈ 3.1 hr).

**Suggested correction:** "Llama 3 §3.3: 16,384 H100 cluster, ~one unexpected interruption every 3 hours."

### 2b. "Section 4: 466 failures over 54-day training run"

**Verdict: Correct paper, wrong section, statistic needs care.** Confidence: high.

§3.3 says: **466 total interruptions = 47 planned + 419 unexpected** over the 54-day pre-training snapshot of the 405B model. Quoting "466 failures" overstates because 47 were scheduled maintenance. Most secondary sources cite the **419 unexpected** number.

**Suggested correction:** "Llama 3 §3.3: 466 total interruptions (419 unexpected) over a 54-day pretraining snapshot."

### 2c. "Six distinct silent data corruption events documented"

**Verdict: Verified with caveat.** Confidence: medium.

The paper's Table 5 (in §3.3) classifies the 419 unexpected interruptions, and SDC is one suspected category. The "6 SDC events" figure is widely cited in secondary literature (e.g., the SDC reliability survey arXiv:2502.12340 cites it directly to the Llama Team 2024 report). I did not pull a clean primary-source quote for the literal "6" because the arXiv PDF wouldn't render text in the WebFetch tool, but the figure is consistent across multiple high-credibility secondary sources.

**Recommendation:** Keep but cite as "Llama 3 §3.3, Table 5" rather than "Section 4." If maximally bulletproof, soften to "approximately six SDC events" or quote a secondary source until you can pull the table directly.

---

## 3. Dixit et al. 2021 — arXiv:2102.11245 — "Silent Data Corruptions at Scale"

**Verdict: Verified.** Confidence: high.

Paper exists at arXiv:2102.11245. Authors: Harish Dattatraya Dixit, Sneha Pendharkar, Matt Beadon, Chris Mason, Tejasvi Chakravarthy, Bharath Muthiah, Sriram Sankar (Meta/Facebook). Abstract directly addresses SDCs not captured by CPU error reporting, datacenter case study, and hundreds of thousands of machines monitored over 18+ months.

Note: this paper does not itself coin "Mercurial Cores" — that term is from Hochschild et al. (citation #4). Dixit's parallel framing is "fault-prone CPU cores" / "corrupting CPUs". If your README implies Dixit coined "Mercurial Cores," that should be Hochschild instead. They are companion papers; both appeared in 2021.

---

## 4. Hochschild et al., HotOS 2021 — "Cores that don't count"

**Verdict: Verified.** Confidence: high.

Authors: Peter H. Hochschild, Paul Turner, Jeffrey C. Mogul, Rama Govindaraju, Parthasarathy Ranganathan, David E. Culler, Amin Vahdat (Google). Venue: HotOS '21 (Workshop on Hot Topics in Operating Systems), Ann Arbor, May 31–June 2 2021. ACM DOI: 10.1145/3458336.3465297. This is the paper that introduced the "Mercurial cores" / Corrupt Execution Errors (CEE) framing. Pairs with Dixit et al.

---

## 5. Hu et al. NSDI 2024 — Shanghai AI Lab characterization

Paper: *Characterization of Large Language Model Development in the Datacenter*, Qinghao Hu et al., NSDI '24. Also arXiv:2403.07648. Studies a 6-month trace from Acme datacenter (Seren + Kalos clusters, 4,704 A100s).

### "22% of jobs fail; 73% due to hardware (not user error)"

**Verdict: Wrong claim.** Confidence: high.

Neither 22% nor 73% appears in the paper. What the paper actually reports:

- **~40% of jobs fail** ("Approximately 40% of jobs fail, with completed jobs consuming only 20∼30% of GPU resources").
- **Infrastructure failures = 82% of failed-job GPU-time but only ~11% of failed-job count** (Table 3). I.e. hardware failures are *rare but expensive*.
- Within infrastructure: NVLink errors 30.25%, CUDA errors 15.77%, node failure 14.30%, ECC 11.00% (shares of infrastructure failures, not all jobs).
- Script errors are the majority of failures *by count* but burn very little GPU-time.

**Suggested correction:** "Hu et al. NSDI 2024: ~40% of jobs fail; infrastructure (hardware) failures account for 82% of failed GPU-time despite being only 11% of failed jobs."

That framing is more striking than the original claim and is actually in the paper.

---

## 6. XPUTimer / Flare — "arXiv:2502.02670"

**Verdict: Wrong arXiv ID.** Confidence: high.

arXiv:2502.02670 is "Machine-learning approaches to accelerating lattice simulations" by Scott Lawrence — completely unrelated.

The correct ID is **arXiv:2502.05413**: *XPUTimer: Anomaly Diagnostics for Divergent LLM Training in GPU Clusters of Thousand-Plus Scale*. Authors are from Shanghai Jiao Tong University, **Ant Group**, and NUS. The paper does cover CUDA-GDB-based intra-kernel tracing for kernel-level fault detection. Note: an alternate version of the title on arXiv lists the system as "Flare" (consistent with the GitHub repo `ant-research/FLARE`) — Flare and XPUTimer appear to be the same / closely related system. Deployed on 6,000+ GPU clusters.

**Suggested correction:** Change ID to **arXiv:2502.05413**. Decide whether to call it XPUTimer or Flare — the GitHub repo is FLARE, the arXiv PDF title is XPUTimer in v1, Flare in later listings. Safest: "XPUTimer/Flare (Ant Group), arXiv:2502.05413."

---

## Confidence notes

- **High confidence** (1a, 1b, 1c, 2a, 2b, 3, 4, 5, 6): I read the actual paper text via arXiv HTML rendering or proceedings, and either confirmed or directly disconfirmed the claim.
- **Medium confidence** (2c): the "six SDC events" figure is repeated by multiple secondary sources (Tom's Hardware, the SDC reliability survey) and is consistent with the paper's Table 5 in §3.3, but the arXiv PDF wouldn't render cleanly in my fetch tool, so I didn't get a verbatim primary-source quote of the literal "six."
- **No low-confidence items** in this batch.

---

## Adjacent papers worth citing

While verifying, I found three papers that fit scanprobe's positioning and would strengthen the README:

1. **MegaScale** — arXiv:2402.15627 (ByteDance, 2024). *Scaling Large Language Model Training to More Than 10,000 GPUs*. Predecessor to ByteRobust. Heavy on practical fault-detection at scale.

2. **Yao et al., NSDI 2025** — *Localizing Irregularities in LLM Training with Mega-scale*. NSDI '25 paper at usenix.org/system/files/nsdi25-yao.pdf. Direct successor to Hu et al. NSDI 2024 and probably more relevant to scanprobe than the 2024 paper, since it's specifically about *localizing* failures rather than just characterizing them.

3. **Understanding Silent Data Corruption in LLM Training** — arXiv:2502.12340 (also ACL 2025). Directly studies SDC as a reliability problem in LLM training and *cites* Llama 3's six-SDC-event figure as a primary motivation. Useful both as a citable claim source and as evidence the problem space is alive in the literature.

Optional but worth a look: **TU Berlin's "Silent Data Corruption: A Major Reliability Challenge in Large-Scale LLM Training"** (semiengineering.com coverage; full paper at arXiv:2604.00726) — survey-style; useful for framing.

---

## Recommended README edits

Drop-in replacements:

1. ByteRobust §4.1 → **keep**.
2. ByteRobust §4.3 EUD recall → **change to §9**.
3. ByteRobust §5.2 dormant faults → **rewrite or drop**; "step 450+" has no source.
4. Llama 3 §4 → **change to §3.3** (everywhere).
5. Llama 3 "466 failures" → "466 total interruptions (419 unexpected)".
6. Hu et al. "22%/73%" → "~40% of jobs fail; infrastructure failures = 82% of failed GPU-time, 11% of failed job count".
7. XPUTimer arXiv ID → **2502.05413** (not 2502.02670).

Sources:
- [Robust LLM Training Infrastructure at ByteDance (HTML)](https://arxiv.org/html/2509.16293v4)
- [The Llama 3 Herd of Models](https://arxiv.org/abs/2407.21783)
- [Llama 3 paper notes — Fan Pu Zeng](https://fanpu.io/blog/2024/llama-3.1-technical-report-notes/)
- [Tom's Hardware: 419 unexpected interruptions](https://www.tomshardware.com/tech-industry/artificial-intelligence/faulty-nvidia-h100-gpus-and-hbm3-memory-caused-half-of-the-failures-during-llama-3-training-one-failure-every-three-hours-for-metas-16384-gpu-training-cluster)
- [Silent Data Corruptions at Scale, Dixit et al.](https://arxiv.org/abs/2102.11245)
- [Cores that don't count, Hochschild et al. HotOS '21](https://dl.acm.org/doi/10.1145/3458336.3465297)
- [Characterization of LLM Development in the Datacenter, Hu et al. NSDI '24](https://www.usenix.org/conference/nsdi24/presentation/hu)
- [Hu et al. NSDI '24 (arXiv HTML)](https://arxiv.org/html/2403.07648v2)
- [XPUTimer/Flare, arXiv:2502.05413](https://arxiv.org/abs/2502.05413)
- [FLARE GitHub (Ant Research)](https://github.com/ant-research/FLARE)
- [Understanding Silent Data Corruption in LLM Training, arXiv:2502.12340](https://arxiv.org/html/2502.12340v1)
- [Localizing Irregularities in LLM Training, Yao et al. NSDI '25](https://www.usenix.org/system/files/nsdi25-yao.pdf)
- [MegaScale, arXiv:2402.15627](https://arxiv.org/html/2402.15627v1)
