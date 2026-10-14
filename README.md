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
python3 sweep.py results/name.csv 1x5 4x5 8x5      # adds UEs as needed, appends one CSV row per step
```
Per step it records throughput, loss, RTT, UPF CPU, sessions, and loss inside the UPF.

Known limit: above ~12 UEs the UERANSIM `ue` container uses 2-3 of the VM's 4 cores and cannot send the full rate, so those steps measure the simulator, not the UPF. Results so far: `results/calib.csv` (1 UE, valid), `results/baseline.csv` (valid up to ~12 UEs; stopped at 32).

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
