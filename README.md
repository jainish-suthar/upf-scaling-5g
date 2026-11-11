# Load-Aware UPF Scaling in a 5G Core

Open5GS 2.8.0 (core) + UERANSIM 3.2.6 (gNB, UEs) + one iperf3 data network, all in Docker Compose.
Find the load at which one UPF degrades, add a second UPF automatically when that load is reached, and show that it helps.
Design: `DESIGN.md` · full write-up (config parameters, results, limitations): **[WRITEUP.md](WRITEUP.md)**.
Report (IEEE format, 5 pages): [report/main.pdf](report/main.pdf), source `report/main.tex`.

![Architecture](architecture.png)

## Results

Final measurements: 12-core Linux laptop, AC power, CPU governor `performance`, nothing else running (host load 0.3 at the start), 2026-11-10. Each UPF is capped at **0.1 core** (`UPF_QUOTA=1000`). Everything comes from one `final.sh` session: data in `results/final/`, tables in [`results/final/summary.md`](results/final/summary.md), host state in `results/final/host.txt`.

**Where one UPF degrades** (phase 3): 4 UEs, 4 shuffled runs per load, 40/40 valid, medians:

| Offered (Mbit/s) | 10 | 20 | 30 | 40 | 50 | 60 | 70 | 80 | 100 | 120 |
|---|---|---|---|---|---|---|---|---|---|---|
| Delivered | 10.0 | 20.0 | 30.0 | 39.8 | 49.7 | 59.4 | 66.4 | 67.7 | 66.5 | 63.2 |
| Loss (%) | 0.0 | 0.0 | 0.02 | 0.4 | 0.7 | 1.0 | 5.1 | 15.4 | 33.5 | 47.3 |
| UPF CPU (% of cap) | 23 | 42 | 58 | 71 | 87 | 95 | 98 | 98 | 98 | 98 |
| Throttled periods (%) | 0.3 | 0.9 | 1.7 | 1.5 | 7.6 | 66.7 | 99.7 | 99.7 | 99.6 | 99.6 |
| RTT p95 (ms) | 0.33 | 0.31 | 0.30 | 0.29 | 0.29 | 3.7 | 10.4 | 13.3 | 15.4 | 16.5 |

![Phase 3 sweep](results/final/threshold.png)

**One UPF vs scaling** (phase 7): 8 UEs join one every 20 s, each sending 12 Mbit/s of UDP = **96 Mbit/s** (~1.4x one UPF). 3 runs per set-up, interleaved, fresh stack each run, 0 failed UEs. Steady state before the first UE leaves, mean (min–max):

| Set-up | Delivered (Mbit/s) | Loss (%) | RTT (ms) | upf1 CPU (% of cap) | UEs on upf2 |
|---|---|---|---|---|---|
| 1 UPF | 69.3 (68.8–69.6) | **27.9** (27.5–28.3) | 8.7 (8.4–9.1) | 100 | 0 of 8 |
| Controller adds upf2 (at 69–72 s) | 92.3 (91.2–93.6) | **3.9** (2.5–5.0) | 3.7 (3.5–4.1) | 100 | 2 of 8 |
| 2 UPFs from the start | 96.0 (96.0–96.0) | **0.0** | 0.8 (0.7–1.0) | 90 | 4 of 8 |

![Phase 7 timelines](results/final/ramp/single_r2_ramp.png)

**Scale-down** (phase 8): the same runs continue: after a 60 s hold, one UE leaves (clean deregistration) every 20 s. 3 runs per variant, mean (min–max). "Fits on one" = offered load below 45 Mbit/s, where upf1 reaches the 80% scale-up limit:

| Variant | upf2 stopped at | upf2 running while empty | 2 UPFs after load fits on one | Traffic lost |
|---|---|---|---|---|
| fifo: oldest UEs leave first | 365 s (365–366) | 25 s | 64 s (64–65) | none |
| lifo: newest leave first, upf2 empties early | 366 s (365–366) | **86 s** (85–86) | 65 s (64–65) | none |
| lifo + `--empty-down` (stop upf2 once empty while upf1 < 80%) | **326 s** (325–326) | 45 s (44–46) | **25 s** (24–25) | none |
| upf2last: a long-lived session stays on upf2 for 90 s more | 451 s (450–451) | 0 s | **150 s** (149–150) | none |
| upf2last + `--drain-timeout 30` (stop upf2 anyway) | 381 s (380–381) | 0 s | 80 s (79–80) | **1 session cut: 10.7 s outage (10–12), new IP** |

![Phase 8: lifo vs lifo with --empty-down](results/final/ramp/lifo_r2_ramp.png)

**In short:**
- **Capacity:** one UPF carries ~68 Mbit/s; strain (throttling, RTT tail) starts at ~60 Mbit/s.
- **Controller:** scales up at **CPU ≥ 80% of the cap for 5 s** (~45 Mbit/s, about 3/4 of the knee), 15/15 runs at 69–73 s; upf2 usable 3.3–6.5 s after the controller acts. No core restart.
- **Scale-up helps, but only partly:** loss 27.9% → 3.9%, not 0%: existing sessions never move and round-robin still sends every other new UE to the busy upf1 (phase 6: 18/18 sessions round-robin).
- **Graceful scale-down is safe but unbounded:** no packets lost in the 12 runs that waited (mean loss at most 0.06%), but upf2 stays until its last session ends: one long-lived session kept it 150 s past the point one UPF would do.
- **Stopping upf2 once it is empty** (`--empty-down`) retires it 40 s sooner at no cost. **Forcing** it bounds the wait but cuts the session: ~11 s outage and a new UE IP.
- **Thresholds carry over between host settings:** an earlier calibration (2026-10-19, slower CPU setting: capacity ~46 Mbit/s) scaled up at about the same 3/4 of its knee with the same % thresholds. See [WRITEUP.md](WRITEUP.md) for both runs and all limitations.

## Requirements
Linux with Docker + Compose (final runs) or Docker Desktop (Mac, for debugging). Give Docker at least 4 CPUs / 4 GB. Python 3 with matplotlib for the plots.
Run long experiments with nothing else open (the laptop has 7 GB of RAM) and on AC power. On Linux, prefix them with `systemd-inhibit --what=sleep:idle:handle-lid-switch` so the host cannot sleep.

## Phase 1: Baseline, 1 UPF, multiple UEs

Start (about 30-55 s until all UEs are up; the first run also builds the images):
```
NUM_UES=5 docker compose up -d
```

Check:
```
docker compose logs ue | grep "TUN interface"                 # one line per UE with its IP
docker compose exec ue ping -I uesimtun0 10.10.3.30           # UE -> UPF -> data network
docker compose exec ue iperf3 -B <UE-IP> -c 10.10.3.30 -t 5   # throughput through that UE
```
`uesimtun0..N-1` are the UEs' tunnel interfaces; each is one PDU session.
After the host sleeps, UE tunnels stay up but carry nothing: `docker compose restart ue`.

More than 60 UEs: raise the default in `config/subs.js` (`60`) and the `dn` iperf3 port range (5201-5260) in `docker-compose.yml`, then `docker compose down` first so Mongo re-seeds.

Stop and wipe everything: `docker compose --profile scale down -v`

Logs: `docker compose logs core | upf1 | gnb | ue`

## What is running
| Container | IPs | Role |
|---|---|---|
| `mongo` | db net | subscriber database (keys, slice, session settings) |
| `core` | N2 10.10.1.10, N4 10.10.2.10 | control plane: NRF, SCP, UDR, UDM, AUSF, PCF, NSSF, SMF, AMF |
| `upf1` | N3 10.10.1.20, N4 10.10.2.20, N6 10.10.3.20 | user plane: forwards UE traffic |
| `upf2` | N3 10.10.1.21, N4 10.10.2.21, N6 10.10.3.21 | second UPF, `scale` profile: only runs after scale-up |
| `gnb` | 10.10.1.30 | UERANSIM base station |
| `ue` | 10.10.1.31 | UERANSIM; runs N simulated UEs |
| `dn` | N6 10.10.3.30 | data network: iperf3 server, ping target |

Networks: `n2n3` 10.10.1.0/24 (gNB, AMF, UPFs), `n4` 10.10.2.0/24 (SMF–UPFs), `n6` 10.10.3.0/24 (UPFs–DN), `db` (core–mongo).

## Conversations between the parts
| Link | Protocol / port | Carries |
|---|---|---|
| UE ↔ gNB | UDP 4997 (simulated radio) | RRC/NAS messages, and user packets |
| gNB ↔ AMF (N2) | SCTP 38412 (NGAP) | NG setup, UE registration, "set up this PDU session" |
| SMF ↔ UPF (N4) | UDP 8805 (PFCP) | association, then per-session rules (which tunnel, which UE IP) |
| gNB ↔ UPF (N3) | UDP 2152 (GTP-U) | UE packets wrapped in a tunnel, tagged with a tunnel ID (TEID) |
| UPF ↔ DN (N6) | plain IP, NAT to the UPF's N6 address | UE packets, so the DN replies to the UPF that sent them |
| NFs inside `core` | HTTP/2 on 127.0.0.x:7777 (SBI) | NF discovery (NRF/SCP), auth, subscriber data, session policy |
| UDR → mongo | MongoDB | subscriber lookups |

## Phases 2-3: Load sweep and threshold
Each step `NxR` = N UEs, each sending R Mbit/s UDP uplink:
```
docker compose down && UPF_QUOTA=1000 NUM_UES=1 docker compose up -d
python3 sweep.py results/final/threshold.csv --reps 4 --shuffle 1 4x2.5 4x5 4x7.5 4x10 4x12.5 4x15 4x17.5 4x20 4x25 4x30
PYTHONNOUSERSITE=1 python3 plot.py sweep results/final/threshold.csv
```
- **What each step records:** delivered throughput, loss, RTT, UPF CPU (% of its cap), % of 10 ms periods throttled, sessions, loss inside the UPF, host load and power.
- **Monitoring:** stats are read through the Docker API, never by `docker exec` into a UPF. Each such exec costs ~30 ms of CPU charged to the UPF's own budget.
- **Robustness:** it resets iperf3 between steps, uses only UEs whose tunnel really carries traffic, and restarts the UE container if tunnels die. Failed steps are retried, and every attempt is kept with a `valid` flag.
- **Cap:** use a new CSV per cap (the cap is not a column). The thresholds in `controller.py` are % of the cap: re-derive them for another `UPF_QUOTA` or host.

## Phase 4: Second UPF
`upf2` is in `docker-compose.yml` under the `scale` profile with its own N3/N4/N6 addresses (10.10.x.21). The SMF is started with both UPFs listed:
```
UPF_N4_IPS="10.10.2.20 10.10.2.21" NUM_UES=1 docker compose up -d      # only upf1 runs
docker compose --profile scale up -d --no-deps upf2                     # bring upf2 in, no SMF restart
```
The SMF retries the association with a listed but absent UPF every ~11 s, so upf2 is associated 0–11 s after it starts (3.8–5.9 s measured). New PDU sessions alternate between associated UPFs; existing sessions stay where they are.

## Phase 5: Controller
```
python3 controller.py results/controller.csv [--drain-timeout S] [--empty-down]
```
- **Scale up:** when upf1's CPU is ≥ 80% of its cap (or ≥ 50% throttled while CPU ≥ 75%) for 5 s (3 s smoothing), it starts upf2 and waits for the SMF's PFCP association.
- **Scale down:** when total UPF CPU is < 35% of one cap for 20 s **and upf2 has 0 sessions**, it stops upf2. A 30 s cooldown separates actions. An unreadable session count counts as "still has sessions".
- **`--empty-down`:** also stops upf2 once it has had 0 sessions for 20 s while upf1 alone is under the 80% scale-up limit (an empty upf2 carries nothing, so no load moves).
- **`--drain-timeout S`:** if upf2 still has sessions S s after the low-load condition held, stop it anyway (cuts them; see phase 8).
- **Measured:** 15/15 scale-ups at 69–73 s under the final ramp, upf2 usable 3.3–6.5 s later; scale-down in 15/15 runs (phase 8).

Things to know:
- UEs must leave cleanly (`./nr-cli <imsi> -e "deregister switch-off"`). A UE killed without deregistering was still counted on upf2 60 s later, so upf2 would never be retired.
- Stopping a UPF that still has sessions cuts them: the SMF notices only by missed PFCP heartbeats (10–16 s measured), releases the session, and the UE re-establishes it on the other UPF with a new IP.
- Scale-up only helps new sessions. With the existing sessions on upf1, upf2 stays idle until UEs (re)register.
- `upf.sh` `exec`s the UPF, so if the UPF exits its container stops instead of looking `Up`.

## Phase 6: Session distribution check
```
docker compose --profile scale down -v
UPF_QUOTA=1000 UPF_N4_IPS="10.10.2.20 10.10.2.21" NUM_UES=1 docker compose --profile scale up -d    # both UPFs up before the UEs
python3 session_trace.py results/linux/trace_r1 --ues 6
```
- **Captures:** PFCP on N4 (inside `core`) and GTP-U on N3 (inside `gnb`, so the UPFs pay nothing). It registers 6 new UEs and pings once through each.
- **Output:** `{n4,n3}.pcap` (open in Wireshark) and `sessions.csv`. Per new session: the UPF the SMF sent the PFCP Session Establishment to, the N3 TEID, and the UPF that UE's GTP-U packets actually reached.
- **Printed summary:** how often the UPF changed between consecutive sessions (strict round-robin = every time) and whether PFCP and GTP-U agree. `--parse dir` re-reads saved pcaps.

## Phases 7-8: Single vs multi-UPF comparison, and scale-down
`ramp.py` adds one UE every `--gap` s; each sends `--rate` Mbit/s UDP until `--hold` s after the last one, so load climbs while sessions are being placed.
With `--down-gap G`, UEs then stop and deregister one every G s (`--order fifo|lifo|upf2-last`, `--linger S` keeps the last one S s longer), followed by `--tail` s with no UEs, so the same run shows scale-down. `compare.sh` runs three set-ups REPS times, interleaved, fresh stack each run, then prints the table (`summarize.py`):
```
OUT=results/linux/compare UPF_QUOTA=1000 sh compare.sh 3 --ues 8 --rate 8 --gap 20 --hold 60     # FIRST=2 resumes at rep 2
python3 plot.py ramp results/linux/compare/single_r2 results/linux/compare/scaled_r2 results/linux/compare/static_r2
```
- **The three set-ups:**
  - `single`: one UPF only
  - `scaled`: both UPFs listed, only upf1 running, the controller decides
  - `static`: both UPFs up from the start
- **Load:** pick `--rate` × `--ues` above the one-UPF capacity but below two UPFs' capacity (here 64 Mbit/s vs ~46 and ~92).
- **Outputs:**
  - `_timeline.csv`: per second, offered/delivered, loss, RTT, per-UPF CPU/throttling/sessions, controller actions
  - `_ues.csv`: per UE, the UPF it landed on, delivered, loss, RTT
  - `_controller.csv`: the controller's log
- **Per-second data:** throughput and loss come from the dn's per-second iperf3 server logs, so a UE that dies mid-run still contributes its seconds.

**Final session** (`final.sh`, ~3.5 h, everything above in one go): host state, phase 3 sweep, per-UE rate picked from the knee (8 UEs ≈ 1.4x one UPF), then 3 interleaved repetitions of 7 set-ups, each ramping up and down: `single`, `static`, and with the controller `fifo`, `lifo`, `upf2last`, `forced` (`--drain-timeout 30`), `lifoearly` (`--empty-down`). Run it with nothing else open, detached from the terminal:
```
nohup systemd-inhibit --what=sleep:idle:handle-lid-switch sh final.sh > results/final/final.log 2>&1 &
tail -5 results/final/progress.log                                      # progress
python3 summarize.py --down --fits 45 results/final/ramp/{fifo,lifo,lifoearly,upf2last,forced}_r*_ues.csv   # scale-down table
```
`downscale.sh` runs only the scale-down variants. A user-installed NumPy 2 breaks the system matplotlib: plot with `PYTHONNOUSERSITE=1`.

## Validity
- **Instrumentation:** stats are read from outside the UPF containers. Measured from inside with `docker exec`, each probe cost ~32 ms of CPU charged to the UPF's own budget: at 0.25 core and only 10 Mbit/s the UPF was throttled in ~23% of periods, versus 0% when read from outside. Those early runs were discarded. Every attempt is kept with a `valid` flag, load order is randomized, and power state, host load and UE restarts are logged per row.
- **Final runs** (`results/final/`, 2026-11-10): Linux, AC, governor `performance`, nothing else open, one session:
  - Phase 3: 4 runs per load, 40/40 valid
  - Phases 7-8: 3 runs per set-up x 7 set-ups, 21/21 without failed UEs or radio link failures
- **Earlier runs** (`results/linux/`, 2026-10-19, slower CPU setting): phase 3 (32/32 valid), phase 6 (3 traced runs) and phase 7 (9/9). Phase 6 and the phase 4-5 checks were not repeated; they test behaviour (placement, association), not capacity. One phase 7 repetition there was discarded after the host ran out of memory (other apps open).
- **Still limited:**
  - The 0.1-core / 10 ms quota is an artificial limit, so the absolute Mbit/s are not real UPF capacities
  - Uplink UDP only
  - One gNB, one DNN/slice, at most 2 UPFs
  - One boot of the stack per phase 3 sweep

## Phases
- [x] 1. Baseline, 1 UPF, multiple UEs
- [x] 2. Load sweep and metrics (instrumentation fixed, see Validity)
- [x] 3. Threshold (0.1 core: strain from ~60 Mbit/s, capacity ~68 Mbit/s; 4 runs per load, 40/40 valid)
- [x] 4. Second UPF (starts late; the SMF retries association every ~11 s, so it is used 4-6 s after start; no SMF restart)
- [x] 5. Controller (scale-up at CPU >= 80% of cap in 15/15 runs at 69-73 s; scale-down only at 0 sessions, or forced / when empty)
- [x] 6. Session distribution check (strict round-robin, PFCP and GTP-U agree on 18/18 sessions in 3 runs)
- [x] 7. Single vs multi-UPF comparison (3 runs each at 96 Mbit/s: loss 27.9% -> 3.9% scaled, 0.0% static)
- [x] 8. Scale-down (5 variants x 3 runs: graceful drain loses nothing but can hold upf2 150 s; `--empty-down` 40 s sooner; forced = ~11 s outage)
- [x] 9. Write-up ([WRITEUP.md](WRITEUP.md): config parameters for multi-UPF, results, limitations)
