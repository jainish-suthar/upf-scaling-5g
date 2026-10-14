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

## Phase 2 — Load sweep (in progress)
UPF is capped at 0.25 core (`UPF_QUOTA` in `docker-compose.yml`). Each step `NxR` = N UEs, each sending R Mbit/s UDP:
```
python3 sweep.py -r 3 results/name.csv 1x5 4x5 8x5   # adds UEs as needed; -r 3 runs the step list 3 times
```
- `results/name.csv`: one row per run: throughput, loss, RTT, UPF CPU (avg/peak), **UPF throttling** (`upf1_throttled_pct` = share of 10 ms CPU periods in which the UPF hit its quota), sessions, gNB/UE CPU, plus the setup (`host`, `open5gs` version, `upf_cap_cores`, `monitor`).
- `results/name_summary.csv`: mean and std per step over the **valid** runs.
- `results/name_samples.csv`: per-second samples of every run: UPF CPU, throttling, gNB/UE cores, probe RTT and probe loss (`warmup=1` marks the 3 warm-up seconds). Use it for time-series plots and for choosing the controller's hold time. Throttling swings by ±10 points from one second to the next even at a steady load.
- RTT (`rtt_*`, `probe_lost_pct`) comes from one extra **idle probe UE** that sends no iperf3 traffic, so it shows queueing in the UPF, not the sender's own backlog. RTT from the first loaded UE is kept as `rtt_loaded_*`. A step with N UEs therefore registers N+1 UEs, so at most 59 traffic UEs fit with 60 subscribers.
- A run is `valid=0` (reason in `invalid_reason`) if any iperf3 failed, the generator sent < 95% of the offered load, or the gNB used ≥ 0.9 core. Those rows measure the simulator, not the UPF.
- Before each step every UE tunnel must ping the DN, otherwise the sweep stops. After the host sleeps, the UE tunnels are dead until `docker compose restart ue`.
- On Linux the sweep keeps the host awake (`systemd-inhibit`, lid included) while it runs.

Monitoring runs from the host (cgroup files under `/sys/fs/cgroup`, counters from `/proc/<pid>/net/dev`), never by `docker exec` into a UPF. Each `docker exec` costs ~30 ms of CPU charged to the UPF's 0.25-core budget. On macOS (Docker Desktop) the host cannot see these files, so the sweep falls back to `docker exec`, and the CSV marks this with `monitor=docker-exec`.

**Older results are skewed by that overhead.** `results/calib.csv`, `results/baseline.csv` and `results/smoke-gomtesh.csv` were measured with `docker exec` monitoring, which throttled the UPF. Example: at 1×80 Mbit/s the smoke run showed 16% loss; with host-side monitoring on the same machine it shows 0% loss. Re-measure before using any of them for the threshold.

## Phase 4 — Second UPF (written, not yet tested)
`upf2` exists in `docker-compose.yml` under the `scale` profile with its own N3/N4/N6 addresses (10.10.x.21), and the SMF can list several UPFs:
```
UPF_N4_IPS="10.10.2.20 10.10.2.21" docker compose --profile scale up -d
```

## Phases (updated as we go)
- [x] 1. Baseline, 1 UPF, multiple UEs
- [~] 2. Load sweep and metrics (script works; generator limit above ~12 UEs)
- [ ] 3. Threshold (single-UE knee ~80-120 Mbit/s at 0.25 core; needs repeat runs)
- [~] 4. Second UPF (config written, untested)
- [ ] 5. Controller (scale up / down)
- [ ] 6. Session distribution check
- [ ] 7. Comparison
- [ ] 8. Write-up
