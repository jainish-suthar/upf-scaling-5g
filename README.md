# Load-Aware UPF Scaling in a 5G Core

Open5GS (core) + UERANSIM (gNB, UEs) + one iperf3 data network, all in Docker Compose.
Design and phases: see `DESIGN.md`.

## Requirements
Docker Desktop (Mac) or Docker + Compose (Linux). Give Docker at least 4 CPUs / 4 GB.

## Phase 1 — Baseline: 1 UPF, multiple UEs

Start (about 55 s until all UEs are up; the first run also builds the images):
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

More than 60 UEs: raise the default in `config/subs.js` (`60`) and the `dn` iperf3 port range (5201-5260) in `docker-compose.yml`, then `docker compose down` first so Mongo re-seeds.

Stop and wipe everything: `docker compose down`

Logs: `docker compose logs core | upf1 | gnb | ue`

## What is running
| Container | IPs | Role |
|---|---|---|
| `mongo` | db net | subscriber database (keys, slice, session settings) |
| `core` | N2 10.10.1.10, N4 10.10.2.10 | control plane: NRF, SCP, UDR, UDM, AUSF, PCF, NSSF, SMF, AMF |
| `upf1` | N3 10.10.1.20, N4 10.10.2.20, N6 10.10.3.20 | user plane: forwards UE traffic |
| `gnb` | 10.10.1.30 | UERANSIM base station |
| `ue` | 10.10.1.31 | UERANSIM; runs N simulated UEs |
| `dn` | N6 10.10.3.30 | data network: iperf3 server, ping target |

Networks: `n2n3` 10.10.1.0/24 (gNB, AMF, UPF), `n4` 10.10.2.0/24 (SMF–UPF), `n6` 10.10.3.0/24 (UPF–DN), `db` (core–mongo).

## Conversations between the parts
| Link | Protocol / port | Carries |
|---|---|---|
| UE ↔ gNB | UDP 4997 (simulated radio) | RRC/NAS messages, and user packets |
| gNB ↔ AMF (N2) | SCTP 38412 (NGAP) | NG setup, UE registration, "set up this PDU session" |
| SMF ↔ UPF (N4) | UDP 8805 (PFCP) | association, then per-session rules (which tunnel, which UE IP) |
| gNB ↔ UPF (N3) | UDP 2152 (GTP-U) | UE packets wrapped in a tunnel, tagged with a tunnel ID (TEID) |
| UPF ↔ DN (N6) | plain IP | UE packets, source address = the UE's `10.45.x.x` |
| NFs inside `core` | HTTP/2 on 127.0.0.x:7777 (SBI) | NF discovery (NRF/SCP), auth, subscriber data, session policy |
| UDR → mongo | MongoDB | subscriber lookups |

## Phase 2-3 — Load sweep and threshold (sweep done, threshold provisional)
UPF is capped at 0.25 core (`UPF_QUOTA` in `docker-compose.yml`). Each step `NxR` = N UEs, each sending R Mbit/s UDP:
```
python3 sweep.py results/name.csv --reps 3 4x5 4x10 4x20 4x50    # --idle K adds K silent UEs, --rep0 N offsets the rep label
```
Per step it records delivered throughput, loss, RTT, UPF CPU (% of its cap), % of 10 ms periods throttled, sessions, and loss inside the UPF.
It resets iperf3 between steps, uses only UEs whose tunnel really carries traffic, and restarts the UE container if tunnels die.

Result (`results/threshold.csv`, 4 UEs, 4 randomized repetitions per load, medians; CPU/throttling read via the Docker API):

| offered Mbit/s | 20 | 40 | 60 | 80 | 100 | 120 | 160 | 200 |
|---|---|---|---|---|---|---|---|---|
| UPF CPU % of cap | 24 | 37 | 45 | 51 | 58 | 63 | 74 | 82 |
| throttled % | 1.4 | 0.9 | 2.9 | 7.1 | 6.4 | 9.2 | 20 | 31 |
| loss % | 1.1 | 0.1 | 0.1 | 4.3 | 2.1 | 1.9 | 2.0 | 2.8 |

CPU and throttling rise steadily and are the usable saturation signals. Loss is small and noisy (single runs of 8-15% occur without any explanation in the logged host load); it is clearly above the light-load level only at 160 and 200 Mbit/s (p=0.03, 0.01, uncorrected). **The saturation knee has not been reached at 200 Mbit/s** (UERANSIM cannot send much more). The controller thresholds (CPU >= 70% of cap, or >= 15% throttled while CPU >= 50%) are provisional and untested on this data. `results/old_exec_probe/` holds the earlier runs, which are invalid because the measuring `docker exec` ran inside the UPF's cgroup.

## Phase 4 — Second UPF (done)
`upf2` is in `docker-compose.yml` under the `scale` profile with its own N3/N4/N6 addresses (10.10.x.21). The SMF is started with both UPFs listed:
```
UPF_N4_IPS="10.10.2.20 10.10.2.21" NUM_UES=1 docker compose up -d      # only upf1 runs
docker compose --profile scale up -d --no-deps upf2                     # bring upf2 in (SMF associates in ~3 s, no restart)
```
Observed: new PDU sessions alternate between associated UPFs; existing sessions stay where they are.

## Phase 5 — Controller (scale-down tested; scale-up under load seen in 3 ramp runs)
```
python3 controller.py results/controller.csv
```
Scale up: upf1 over the threshold for 5 s -> starts upf2, waits for the SMF's PFCP association (2-3 s from container start to association). Load-triggered scale-up now works at a 0.1-core cap (`UPF_QUOTA=1000`): in 3 `ramp.py --controller` runs it fired at 17-29 s, once ~30 Mbit/s was offered (upf1 at 60-90% CPU, 25-60% throttled). The thresholds are still the provisional ones; the earlier logged scale-ups (`results/old_exec_probe/`) fired on a measurement artifact. At idle the fixed controller stays quiet (50 s test).
Scale down: total UPF CPU < 25% for 20 s **and upf2 has 0 sessions** -> stops upf2 (measured: 2 s after the last session left, `results/scale_down.csv`).

Things to know:
- UEs must leave cleanly (`./nr-cli <imsi> -e "deregister switch-off"`). Killing UE processes leaves stale sessions in the core for a long time, so upf2 is never retired.
- Stopping a UPF that still has sessions drops them; the SMF takes ~15 s to stop waiting on it.
- Scale-up only helps new sessions. With the existing sessions on upf1, upf2 stays idle until UEs (re)register.
- UERANSIM's radio link sometimes fails ("Radio link failure"), probably Docker VM / laptop stalls. Final timed runs belong on the Linux VM.

## Phase 3 (redo) — Reach the knee with a lower UPF cap (done)
At 0.25 core the UPF is not saturated by the most UERANSIM can send (~200 Mbit/s). From the CPU column above, 100 Mbit/s needs ~0.145 core, so a 0.1-core cap should saturate around 60-70 Mbit/s:
```
docker compose down && UPF_QUOTA=1000 NUM_UES=1 docker compose up -d
python3 sweep.py results/threshold_q1000.csv --reps 4 --shuffle 1 4x5 4x10 4x15 4x20 4x25 4x30 4x40 4x50
python3 plot.py sweep results/threshold.csv results/threshold_q1000.csv     # -> results/threshold_sweep.png
```
Use a new CSV per cap (the cap is not a column).

Result (`results/threshold_q1000.csv`, 0.1 core, 4 UEs, 4 shuffled runs per load, medians; plot `results/threshold_sweep.png`):

| offered Mbit/s | 10 | 20 | 30 | 40 | 50 | 60 | 80 |
|---|---|---|---|---|---|---|---|
| delivered | 9.9 | 19.9 | 29.9 | 39.8 | 49.6 | 58.2 | 69.0 |
| loss % | 0.5 | 0.8 | 0.4 | 0.5 | 0.7 | 2.8 | 13.7 |
| UPF CPU % of cap | 42 | 60 | 75 | 81 | 88 | 92 | 92 |
| throttled % | 11 | 16 | 32 | 53 | 62 | 70 | 82 |

Knee at ~60 Mbit/s (loss up in all 4 runs; throughput flattens at ~69). Controller now scales up at CPU >= 80% (or >= 50% throttled with CPU >= 75%), about 2/3 of the knee, since scale-up takes ~8 s and only helps new sessions. Then set `UP_CPU` / `UP_THROTTLED` in `controller.py` a bit below where loss/RTT rise, and use the same `UPF_QUOTA` for phases 5-7.

## Phase 6 — Session distribution check (first run done)
```
UPF_QUOTA=1000 UPF_N4_IPS="10.10.2.20 10.10.2.21" NUM_UES=1 docker compose --profile scale up -d    # both UPFs up before the UEs
python3 session_trace.py results/trace --ues 6
```
Captures PFCP on N4 (inside `core`) and GTP-U on N3 (inside `gnb`, so the UPFs pay nothing), registers 6 new UEs and pings once through each.
Writes `results/trace/{n4,n3}.pcap` (open in Wireshark) and `sessions.csv`: per new session, the UPF the SMF sent the PFCP Session Establishment to, the N3 TEID, and the UPF that UE's GTP-U packets actually reached.
It prints how many times the UPF changed between consecutive sessions (strict round-robin = every time) and whether PFCP and GTP-U agree. `--parse dir` re-reads saved pcaps.

Result (`results/trace/`, 0.1-core cap, both UPFs up, 6 new UEs, one run): the SMF sent sessions to upf2, upf1, upf2, upf1, upf2, upf1 (switched UPF 5/5 times = strict round-robin), and for all 6 the uplink GTP-U went to the UPF and TEID that PFCP set up.

## Phase 7 — Single vs multi-UPF comparison (one smoke pair run, needs repetitions)
`ramp.py` adds one UE every `--gap` s; each sends `--rate` Mbit/s UDP until `--hold` s after the last one, so load climbs while sessions are being placed. Same ramp twice:
```
# A: one UPF only
docker compose down && UPF_QUOTA=1000 NUM_UES=1 docker compose up -d
python3 ramp.py results/ramp_single --ues 8 --rate 15 --gap 20 --hold 60
# B: both UPFs listed, only upf1 running, controller decides
docker compose down && UPF_QUOTA=1000 UPF_N4_IPS="10.10.2.20 10.10.2.21" NUM_UES=1 docker compose up -d
python3 ramp.py results/ramp_scaled --ues 8 --rate 15 --gap 20 --hold 60 --controller
python3 plot.py ramp results/ramp_single results/ramp_scaled                 # -> results/ramp_single_ramp.png
```
Pick `--rate` x `--ues` so the total is well past the one-UPF knee from phase 3 (e.g. 8 x 15 = 120 Mbit/s at 0.1 core).
Outputs: `_timeline.csv` (per second: offered/delivered, loss, RTT, per-UPF CPU/throttling/sessions, controller actions), `_ues.csv` (per UE: the UPF it landed on, delivered, loss, RTT), `_controller.csv`.
Smoke result (`results/smoke/`, 0.1-core cap, 8 UEs x 15 Mbit/s, one run each, a kind cluster also running on the host, host load ~8):

| last 70 s (120 Mbit/s offered) | delivered Mbit/s | loss % | RTT ms |
|---|---|---|---|
| 1 UPF | 89 | 24 | 6.5 |
| controller (scaled up at 29 s, upf2 associated in 2.6 s) | 113 | 1.3 | 2.3 |

The UEs on upf2 (3, 5, 7) saw ~0.1% loss and ~1 ms RTT; the UEs left on upf1 still saw 1-2% loss with upf1 at ~97% of its cap, because existing sessions never move.

Full comparison: `sh compare.sh 3` runs single / scaled / static (both UPFs from the start) 3 times each, interleaved, and prints the table (`summarize.py`).
A first full run was **not usable**: 13 of 72 UE streams lost their whole result because iperf3 reports only at the end and some UEs died mid-run (radio link failures, high host load). `ramp.py` now reads per-second logs from the dn's iperf3 servers, keeps every second a UE delivered, and flags UEs that stopped early (`stopped_early`, `last_rx_s`); host load is logged per second. Smoke-tested, the full comparison still has to be re-run.

Repeat each run 3+ times (fresh `docker compose down` each). Expect only UEs that arrive after scale-up, and only every other one of them, to land on upf2; compare loss and RTT of those UEs and of the total after scale-up.

## Validity (audit)
Functional behaviour (late UPF joins, round-robin placement, scale-down only at 0 sessions) holds; performance claims are still limited:
- Fixed: stats are read from outside the container (the old `docker exec` probe caused ~4% CPU and ~50% throttling at idle); failed iperf3 steps are retried and every attempt is kept with a `valid` flag; load order is randomized; power state, host load and UE restarts are logged.
- Still open: only 4 repetitions per load, one boot of the stack; runs were on battery power with host load 4-11; the 0.25-core / 10 ms quota is an artificial limit; workload is uplink UDP from 4 UEs only; no evidence yet that a second UPF improves loss or throughput, nor that the controller scales up under real load; the unexplained 8-15% loss bursts.

## Phases
- [x] 1. Baseline, 1 UPF, multiple UEs
- [x] 2. Load sweep and metrics (instrumentation fixed, see Validity)
- [x] 3. Threshold (knee ~60 Mbit/s at 0.1 core; controller thresholds set from it)
- [x] 4. Second UPF (starts late, SMF associates in ~3 s, new sessions alternate)
- [x] 5. Controller (scale-up under load at 0.1 core, thresholds from phase 3; scale-down at 0 sessions)
- [x] 6. Session distribution check (strict round-robin, PFCP and GTP-U agree on 6/6; one run)
- [~] 7. Single vs multi-UPF comparison (measurement fixed; re-run `sh compare.sh 3` on a quiet host)
- [ ] 8. Write-up
