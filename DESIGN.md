# Load-Aware UPF Scaling in a 5G Core — Design

**Goal:** find the load at which one UPF starts to degrade, then add a second UPF automatically when that load is reached, and show it helps.

## Stack
| Part | Tool |
|---|---|
| 5G core (AMF, SMF, NRF, UDM, UPF…) | Open5GS |
| gNB + UEs | UERANSIM |
| Data network (internet stand-in) | one container with iperf3 server + ping target |
| Deployment | Docker Compose (Linux VM for final runs; Mac OK for debugging) |
| Traffic / metrics / controller / plots | Python + Bash, iperf3, tcpdump → Wireshark |

## Topology
```
UE(s) -- gNB --N3-- UPF1 --N6-- data network (iperf3)
                    UPF2 --N6--/
AMF --N2-- gNB        SMF --N4(PFCP)-- UPF1, UPF2
```
Separate Docker networks for N2/N3, N4 and N6. UPF2 gets its own IP on each.
Each UPF container has a fixed CPU limit (e.g. 0.5 core) so it saturates before the traffic generators do.

## Repo layout (kept minimal)
```
docker-compose.yml     core + gNB + data network; UPF2 under a profile
config/                Open5GS + UERANSIM yaml (only what we change)
run_load.py            ramps UEs/traffic, samples metrics, writes CSV
controller.py          the scaling controller
plot.py                CSVs -> graphs
```

## Phases
1. **Baseline:** one UPF, a few UEs, ping through `uesimtun0`.
2. **Load sweep:** raise UEs and traffic in steps. Per step record UPF CPU, throughput, RTT, loss, active sessions.
3. **Threshold:** the load where RTT/loss rise or throughput flattens → pick a CPU threshold a bit below it.
4. **Second UPF:** add UPF2 with its own N3/N4/N6; list both in the SMF config.
5. **Controller:** see below.
6. **Distribution check:** capture N4 (PFCP) and N3 (GTP-U); confirm which UPF each new session lands on.
7. **Compare:** repeat the sweep with scaling on; compare throughput, RTT, loss, CPU, load split.
8. **Write up:** config parameters needed and limitations found.

## Controller
Every second: read each active UPF's CPU (cgroup/`docker stats`) and session count.
- **Scale up:** UPF1 CPU above the high threshold for N seconds → start UPF2. It joins the SMF over PFCP.
- **Scale down:** total load below the low threshold (well under the high one) for M seconds → retire UPF2:
  1. Stop sending it new sessions (release its PFCP association, or take it out of selection — to be confirmed).
  2. Wait for its sessions to end (or release them so the UEs re-establish on UPF1).
  3. Stop the UPF2 container only when it has 0 sessions, so no live session is cut.
- Two thresholds (high/low), a hold time and a cooldown stop flapping. Every decision is logged with a timestamp.

## Things to verify early (not assumed)
- The SMF alternates new sessions between associated UPFs (believed round-robin; confirm in source/trace).
- The SMF does not need a restart when UPF2 joins late (it is pre-listed in config, so only PFCP association happens at runtime).
- Existing sessions stay on UPF1; only new sessions go to UPF2. Experiments must add UEs gradually, or release and re-establish sessions, to show relief.
- Scale-down is the harder half: stopping a UPF drops its sessions, and the SMF may keep selecting a UPF that is going away. Check how Open5GS reacts to a UPF leaving (PFCP release vs. just stopping).
- UERANSIM itself may become the bottleneck before the UPF does; check generator CPU each run.

## Out of scope

Kubernetes, real RAN, multi-slice policy, more than two UPFs.
