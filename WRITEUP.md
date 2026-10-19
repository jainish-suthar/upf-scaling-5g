# Load-Aware UPF Scaling in a 5G Core: Write-up (phase 8)

Open5GS 2.8.0 core + UERANSIM 3.2.6 gNB/UEs + one iperf3 data network, in Docker Compose.
Each UPF is capped at **0.1 core** (`UPF_QUOTA=1000` over a 10 ms period) so that it, not the traffic generator, saturates first.

![Architecture](architecture.png)

**Result:** one UPF at 0.1 core carries about **46 Mbit/s**, and strain starts at about **40 Mbit/s**. With 64 Mbit/s offered, adding a second UPF on demand cut loss from **19.3% to 0.5%** and RTT from **10.0 to 3.0 ms**. Starting with two UPFs gave 0.0% and 0.7 ms. The gap between the two comes from the core's round-robin placement and from sessions never moving (section 3).

All numbers below were measured on one machine: a 12-core Linux laptop (HP Pavilion Gaming 15) on AC power, host load ≤ 1.6 (one run at 4.7), on 2026-10-19. Raw data is in `results/linux/`.

## Results

### Phase 3: where one UPF degrades (`results/linux/linux_threshold_q1000.csv`)

4 UEs, offered load 10–100 Mbit/s, 4 shuffled repetitions per load. 32/32 runs were valid (no failed iperf3, no dead tunnels, no UE restarts). Medians:

| Offered (Mbit/s) | 10 | 20 | 30 | 40 | 50 | 60 | 80 | 100 |
|---|---|---|---|---|---|---|---|---|
| Delivered (Mbit/s) | 10.0 | 20.0 | 29.8 | 39.5 | 45.8 | 46.0 | 43.8 | 40.5 |
| Loss (%) | 0.0 | 0.07 | 0.7 | 1.4 | 8.4 | 23.4 | 45.3 | 59.6 |
| UPF CPU (% of cap) | 33 | 56 | 83 | 95 | 96 | 97 | 97 | 96 |
| Throttled 10 ms periods (%) | 0.8 | 2.9 | 12.6 | 62.5 | 98.7 | 99.6 | 99.5 | 99.5 |
| RTT p95 (ms) | 0.49 | 0.44 | 0.77 | 3.5 | 15.2 | 18.0 | 18.1 | 19.0 |

![Phase 3 sweep](results/linux/linux_threshold_q1000_sweep.png)

- **Capacity:** delivered throughput flattens at about 46 Mbit/s, and past it extra load only adds loss (60% at 100 offered).
- **Knee:** at 40 Mbit/s, loss, the RTT tail and throttling all rise. At 50 Mbit/s the UPF is throttled in 99% of periods.
- **Threshold:** the controller scales up at **CPU ≥ 80% of the cap** (or ≥ 50% throttled while CPU ≥ 75%), held for 5 s. That is ~30 Mbit/s, about 2/3 of the knee, because a scale-up takes 5 s plus up to 11 s for the association and only helps sessions placed afterwards. 20 Mbit/s (56% CPU) never triggers it.
- **Cap, not host speed:** the cap is CPU time (1 ms per 10 ms), so how many Mbit/s fit in it depends on the CPU. Thresholds are therefore kept as % of the cap, and the Mbit/s figures hold for this host only.

### Phases 4–5: second UPF and controller

| Check | Result |
|---|---|
| UPF2 joins with no SMF restart | The SMF retries the PFCP association with a listed but absent UPF about every 11 s. UPF2 started at 14:14:17.9 and was associated at 14:14:21.7 (3.8 s) |
| Scale-up under load (3 runs) | Fired at 71–72 s, right after the 4th UE (32 Mbit/s, upf1 CPU crossing 80%). UPF2 associated 4.5–5.9 s after the controller acted |
| No false scale-up at idle | 35 s idle with both UPFs at < 1% CPU: no scale-up (`results/linux/scale_down_controller.csv`) |
| Scale-down only at 0 sessions | Low load for 35 s, but upf2 held 1 session, so the controller waited. It stopped upf2 in the first second after that UE deregistered (2.6 s wall clock) |
| UPF crash visible | `upf.sh` now `exec`s the UPF, so when the UPF exits the container shows `Exited` instead of staying `Up` (tested with SIGTERM) |

### Phase 6: how new sessions are distributed (`results/linux/trace_r{1,2,3}/`)

PFCP was captured on N4 inside `core` and GTP-U on N3 inside `gnb`, so the UPFs pay nothing for the capture. Both UPFs were up, and 6 new UEs were added per run, 3 runs:

| Run | UPF chosen by the SMF (PFCP Session Establishment), in order | GTP-U reached that UPF with that TEID |
|---|---|---|
| 1 | upf2, upf1, upf2, upf1, upf2, upf1 | 6/6 |
| 2 | upf2, upf1, upf2, upf1, upf2, upf1 | 6/6 |
| 3 | upf2, upf1, upf2, upf1, upf2, upf1 | 6/6 |

That is **strict round-robin in 18/18 sessions**, regardless of load. Each run starts at upf2 because the one UE registered at startup took upf1. The pcaps (`n4.pcap`, `n3.pcap`) open in Wireshark.

### Phase 7: one UPF vs scaling vs two UPFs (`results/linux/compare/`)

The ramp adds one UE every 20 s, each sending 8 Mbit/s of UDP uplink, up to 8 UEs = **64 Mbit/s**: about 1.4× one UPF's capacity, within two UPFs' capacity. Each run used a fresh stack, 3 repetitions per set-up, interleaved; 9/9 runs had no failed UEs or radio link failures. Values are the steady state (10 s after the last UE until the end), mean (min–max):

| Set-up | Delivered (Mbit/s) | Loss (%) | RTT (ms) | upf1 CPU (% of cap) | UEs on upf2 | Scale-up at |
|---|---|---|---|---|---|---|
| 1 UPF | 51.6 (50.7–52.1) | 19.3 (18.5–20.8) | 10.0 (9.3–11.2) | 100 | 0 of 8 | — |
| Controller adds upf2 | 63.7 (63.6–63.8) | 0.5 (0.4–0.7) | 3.0 (2.4–4.1) | 99 | 2 of 8 | 71–72 s |
| 2 UPFs from the start | 64.0 (64.0–64.0) | 0.0 | 0.7 | 85 | 4 of 8 | — |

![Ramp timelines, repetition 2](results/linux/compare/compare_ramp_r2.png)

With one UPF, delivered traffic stops at about 52 Mbit/s once the 6th UE joins (48 Mbit/s offered). With the controller, UEs 5 and 7 land on upf2 (0.0% loss, RTT p95 ~0.4 ms). UEs 6 and 8 still go to upf1 by round-robin, so upf1 carries 6 UEs = 48 Mbit/s at 99% of its cap, and its UEs see 0.3–0.5% loss and RTT p95 of 6–13 ms (all 3 runs).

## 1. Core configuration needed for two UPFs

Adding a UPF in Open5GS needs changes in three places: the SMF's UPF list, the new UPF's own addresses, and the network around it. The SMF is never restarted to add a UPF. It is configured with both from the start, and upf2 joins at runtime by PFCP association.

| Where | Parameter | Value here | Why it is needed |
|---|---|---|---|
| `smf.yaml` | `smf.pfcp.client.upf[].address` | `10.10.2.20`, `10.10.2.21` (`UPF_N4_IPS`) | Every UPF the SMF may use must be listed. The SMF keeps trying to associate with a listed UPF that is down; once it answers, it is used within ~3 s, without an SMF restart |
| `smf.yaml` | `smf.pfcp.server.address` | `10.10.2.10` (N4) | The address the UPFs' PFCP association and heartbeats come back to |
| `smf.yaml` | `smf.session[].subnet` / `gateway` / `dnn` | `10.45.0.0/16`, `10.45.0.1`, `internet` | The SMF hands out UE IPs from this pool for **both** UPFs. Open5GS cannot tie a pool to one UPF in this setup |
| `smf.yaml` (optional) | `upf[].dnn` / `tac` / `nr_cell_id` | not set | Pins sessions to a UPF by DNN, TAC or cell. With none set, the SMF alternates between associated UPFs (verified in phase 6) |
| `upf.yaml` (each UPF) | `upf.pfcp.server.address` | `10.10.2.2x` (N4) | Must equal the address listed in the SMF |
| `upf.yaml` (each UPF) | `upf.gtpu.server.address` | `10.10.1.2x` (N3) | Sent to the gNB in the PDU session setup; the gNB tunnels uplink to it |
| `upf.yaml` (each UPF) | `upf.session[].subnet` + `ogstun` | `10.45.0.0/16`, `10.45.0.1/16` | Each UPF must route the whole UE pool into its TUN device for downlink |
| `upf.sh` (each UPF) | `iptables … MASQUERADE` on N6 | `-s 10.45.0.0/16 -o <N6 dev>` | Both UPFs serve the same pool, so the DN could not tell which UPF owns a UE address. NAT makes replies go back to the UPF that sent them. Without NAT, each UPF needs its own pool plus per-UPF routes on N6 |
| `docker-compose.yml` | `upf2` own N3/N4/N6 IPs, `profiles: [scale]` | `10.10.{1,2,3}.21` | Separate interfaces per UPF (task 4); the profile keeps upf2 off until the controller starts it |
| `docker-compose.yml` | `cpu_quota` / `cpu_period` | `1000` / `10000` | The artificial 0.1-core cap that makes a UPF saturate at a load UERANSIM can generate |
| `amf.yaml`, `gnb.yaml` | nothing UPF-specific | | The gNB learns each session's UPF N3 address from the AMF/SMF; no RAN change is needed |

## 2. How the scaling works

1. `controller.py` reads each running UPF's CPU (share of its cap) and throttled periods every second, smoothed over 3 s, through the Docker API (never `docker exec` into a UPF, which would spend the UPF's own budget).
2. **Scale up:** when upf1 stays over the threshold for 5 s, it starts the `upf2` container. The SMF already lists upf2, so it associates over PFCP and starts placing **new** sessions on it.
3. **Scale down:** when the total UPF CPU stays low for 20 s **and upf2 has 0 sessions**, it stops upf2. Two thresholds, a hold time and a 30 s cooldown prevent flapping.

## 3. Practical limitations of dynamic UPF scaling in Open5GS

| # | Limitation | Evidence | Consequence |
|---|---|---|---|
| 1 | **UPF selection is round-robin, not load-aware.** The only selectors are static (DNN, TAC, cell ID) | Phase 6: 18/18 sessions alternate. Phase 7: after scale-up, every other new UE still goes to the saturated upf1 | Scaling helps only half of the new sessions. A load-aware SMF, or a selector the controller could change, would put all of them on upf2 |
| 2 | **Existing sessions never move** | Phase 7: the 4 UEs placed before scale-up stay on upf1 to the end | Scaling relieves only *future* load. Hence scaled (0.5% loss) vs static (0.0%) |
| 3 | **No graceful drain.** The SMF cannot be told to stop picking a UPF, and a UE that dies without deregistering leaves its session behind | Phase 6: the SMF keeps selecting upf2 while it is associated. A UE killed without deregistering was still counted on upf2 after 60 s | Scale-down waits until upf2's sessions end. UEs must deregister cleanly, or upf2 is never retired |
| 4 | **Stopping a UPF that has sessions cuts them** | upf2 stopped with a live UE on it: the SMF noticed only by missed PFCP heartbeats, 10 s and 16 s later in two runs. It then released the session, and the UE re-established it on upf1 within 0.2 s with a **new IP** (10.45.0.3 → 10.45.0.4) | Such a UE loses 10–16 s of traffic and its address. The controller therefore stops upf2 only at 0 sessions (verified) |
| 5 | **Scale-up takes 5 s plus 0–11 s** | Hold 5 s; the SMF retries the association every ~11 s. Measured 4.5–5.9 s after the controller acted | Thresholds must sit well below the knee (here 2/3), or the burst is over before upf2 helps |
| 6 | **Shared UE pool needs NAT on N6** | Section 1 | The DN sees the UPF's address, not the UE's. Per-UE identity is only visible on N3 (inner IP in GTP-U) |
| 7 | **Thresholds depend on the host and the cap** | The cap is CPU time, so Mbit/s per 0.1 core depends on the CPU; 0.1 core = ~46 Mbit/s on this host | Re-run phase 3 per host and per `UPF_QUOTA`. Keep the thresholds as % of the cap |
| 8 | **Per-second signals are noisy** | Throttled periods swing ±10–15 points second to second at a steady load | The controller smooths over 3 s and requires 5 s above the threshold |
| 9 | **The test bed limits what can be shown** | 0.1-core cap is artificial; UERANSIM tops out ~200 Mbit/s; uplink UDP only; one gNB, one DNN/slice, at most 2 UPFs | Absolute Mbit/s figures are not real UPF capacities. The *shape* (knee, relief, round-robin effect) is the result |
| 10 | **The host must be quiet** | A repetition was discarded after VS Code + Chrome filled RAM/swap (load 88, OOM kills) | Run final measurements with nothing else open. `sweep.py` logs host load and power per row |

## 4. Reproduce

```bash
# phase 3 (≈20 min)
docker compose down && UPF_QUOTA=1000 NUM_UES=1 docker compose up -d
python3 sweep.py results/linux/linux_threshold_q1000.csv --reps 4 --shuffle 1 4x2.5 4x5 4x7.5 4x10 4x12.5 4x15 4x20 4x25

# phase 6 (≈1 min per run): both UPFs up before any UE
docker compose --profile scale down -v
UPF_QUOTA=1000 UPF_N4_IPS="10.10.2.20 10.10.2.21" NUM_UES=1 docker compose --profile scale up -d
python3 session_trace.py results/linux/trace_r1 --ues 6

# phase 7 (≈40 min): 3 repetitions x {1 UPF, controller, 2 UPFs}, fresh stack each run
OUT=results/linux/compare UPF_QUOTA=1000 sh compare.sh 3 --ues 8 --rate 8 --gap 20 --hold 60
python3 plot.py ramp results/linux/compare/single_r2 results/linux/compare/scaled_r2 results/linux/compare/static_r2
```
Keep the host awake during long runs: prefix with `systemd-inhibit --what=sleep:idle:handle-lid-switch`.
