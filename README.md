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

More than 10 UEs: raise the default in `config/subs.js` (`10`), then `docker compose down` first so Mongo re-seeds.

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

## Phase 5 — Controller (written; scale-down tested, scale-up under load not yet)
```
python3 controller.py results/controller.csv
```
Scale up: upf1 over the threshold for 5 s -> starts upf2, waits for the SMF's PFCP association (2-3 s from container start to association). **Load-triggered scale-up has not been demonstrated yet**: the earlier logged scale-ups (`results/old_exec_probe/`) fired on a measurement artifact. At idle the fixed controller stays quiet (50 s test).
Scale down: total UPF CPU < 25% for 20 s **and upf2 has 0 sessions** -> stops upf2 (measured: 2 s after the last session left, `results/scale_down.csv`).

Things to know:
- UEs must leave cleanly (`./nr-cli <imsi> -e "deregister switch-off"`). Killing UE processes leaves stale sessions in the core for a long time, so upf2 is never retired.
- Stopping a UPF that still has sessions drops them; the SMF takes ~15 s to stop waiting on it.
- Scale-up only helps new sessions. With the existing sessions on upf1, upf2 stays idle until UEs (re)register.
- UERANSIM's radio link sometimes fails ("Radio link failure"), probably Docker VM / laptop stalls. Final timed runs belong on the Linux VM.

## Validity (audit)
Functional behaviour (late UPF joins, round-robin placement, scale-down only at 0 sessions) holds; performance claims are still limited:
- Fixed: stats are read from outside the container (the old `docker exec` probe caused ~4% CPU and ~50% throttling at idle); failed iperf3 steps are retried and every attempt is kept with a `valid` flag; load order is randomized; power state, host load and UE restarts are logged.
- Still open: only 4 repetitions per load, one boot of the stack; runs were on battery power with host load 4-11; the 0.25-core / 10 ms quota is an artificial limit; workload is uplink UDP from 4 UEs only; no evidence yet that a second UPF improves loss or throughput, nor that the controller scales up under real load; the unexplained 8-15% loss bursts.

## Phases
- [x] 1. Baseline, 1 UPF, multiple UEs
- [x] 2. Load sweep and metrics (instrumentation fixed, see Validity)
- [~] 3. Threshold (provisional: knee not reached at 200 Mbit/s, needs a lower UPF cap)
- [x] 4. Second UPF (starts late, SMF associates in ~3 s, new sessions alternate)
- [~] 5. Controller (scale-down at 0 sessions works; scale-up under real load still to be shown)
- [ ] 6. Session distribution check (N4/GTP-U traces)
- [ ] 7. Single vs multi-UPF comparison
- [ ] 8. Write-up
