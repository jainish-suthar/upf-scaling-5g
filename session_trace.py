#!/usr/bin/env python3
"""Session distribution check (phase 6).   usage: python3 session_trace.py results/trace_dir [--ues N]     or   python3 session_trace.py --parse results/trace_dir
Captures N4 (PFCP, inside core) and N3 (GTP-U, inside gnb; not inside a UPF so the capture costs the UPFs no CPU),
registers N new UEs, pings once through each new tunnel, then answers per session: which UPF did the SMF send the
session to (PFCP Session Establishment) and which UPF did that UE's packets actually reach (GTP-U outer destination).
Only sessions set up while the capture runs are seen: start the stack with both UPFs up and few UEs."""
import csv, os, struct, subprocess, sys, time
from sweep import P, alive, ensure_ues, run, running, tuns

UPF_N4 = {"10.10.2.20": "upf1", "10.10.2.21": "upf2"}
UPF_N3 = {"10.10.1.20": "upf1", "10.10.1.21": "upf2"}
CAPS = {"n4": ("core", "udp port 8805", 0), "n3": ("gnb", "udp port 2152", 128)}   # PFCP whole; GTP-U only up to the inner IP header


def ip4(b):
    return ".".join(map(str, b))


def udp_packets(path):           # yields (src ip, dst ip, udp payload) for IPv4/UDP packets in a pcap (Ethernet or Linux cooked)
    d = open(path, "rb").read()
    e = "<" if d[:4] in (b"\xd4\xc3\xb2\xa1", b"\x4d\x3c\xb2\xa1") else ">"
    link = struct.unpack(e + "I", d[20:24])[0]
    off, i = {1: 14, 113: 16, 276: 20}[link], 24
    while i + 16 <= len(d):
        n = struct.unpack(e + "I", d[i + 8:i + 12])[0]
        p, i = d[i + 16:i + 16 + n], i + 16 + n
        ip = p[off:]
        if len(ip) < 28 or ip[0] >> 4 != 4 or ip[9] != 17:
            continue
        h = (ip[0] & 15) * 4
        yield ip4(ip[12:16]), ip4(ip[16:20]), ip[h + 8:]


def ies(b):                      # PFCP information elements -> list of (type, value)
    out, i = [], 0
    while i + 4 <= len(b):
        t, n = struct.unpack(">HH", b[i:i + 4])
        out.append((t, b[i + 4:i + 4 + n]))
        i += 4 + n
    return out


def find(b, path):               # values of IE path, e.g. (1, 2, 93) = Create PDR / PDI / UE IP Address
    vals = [v for t, v in ies(b) if t == path[0]]
    return vals if len(path) == 1 else [x for v in vals for x in find(v, path[1:])]


def ue_ip(v):                    # UE IP Address IE: flags, then IPv4 if V4 bit set
    return ip4(v[1:5]) if v[0] & 2 else None


def teid(v):                     # F-TEID IE: flags, then TEID unless CHOOSE is set
    return struct.unpack(">I", v[1:5])[0] if not v[0] & 4 else None


def pfcp_sessions(path):         # one entry per Session Establishment Request (type 50), matched to its Response (51) by sequence number
    sess, by_seq = [], {}
    for src, dst, u in udp_packets(path):
        if len(u) < 8:
            continue
        mtype, s = u[1], u[0] & 1
        hdr = 16 if s else 8
        seq = int.from_bytes(u[hdr - 4:hdr - 1], "big")
        body = u[hdr:4 + struct.unpack(">H", u[2:4])[0]]
        if mtype == 50:
            ips = [x for x in map(ue_ip, find(body, (1, 2, 93))) if x]
            teids = [x for x in map(teid, find(body, (1, 2, 21))) if x]
            r = {"n": len(sess) + 1, "ue_ip": ips[0] if ips else "", "pfcp_upf": UPF_N4.get(dst, dst),
                 "n3_teid": teids[0] if teids else "", "accepted": ""}
            sess.append(r)
            by_seq[(dst, seq)] = r
        elif mtype == 51 and (src, seq) in by_seq:
            r = by_seq[(src, seq)]
            cause = find(body, (19,))
            r["accepted"] = int(bool(cause) and cause[0][0] == 1)
            if r["n3_teid"] == "":   # UPF chose the N3 TEID: it is in Created PDR / F-TEID
                t = [x for x in map(teid, find(body, (8, 21))) if x]
                r["n3_teid"] = t[0] if t else ""
    return sess


def gtpu_flows(path):            # uplink G-PDUs: (UPF it was sent to, TEID, inner source = UE IP) -> packet count
    flows = {}
    for src, dst, g in udp_packets(path):
        if len(g) < 8 or g[1] != 255 or dst not in UPF_N3:
            continue
        t, i = struct.unpack(">I", g[4:8])[0], 8
        if g[0] & 7:             # E, S or PN set: 4 optional bytes, the last one is the next extension header type
            nxt, i = g[11], 12
            while nxt and i < len(g) and g[i]:
                nxt, i = g[i + 4 * g[i] - 1], i + 4 * g[i]
        inner = g[i:]
        if len(inner) >= 20 and inner[0] >> 4 == 4:
            k = (UPF_N3[dst], t, ip4(inner[12:16]))
            flows[k] = flows.get(k, 0) + 1
    return flows


def parse(d):
    sess, flows = pfcp_sessions(f"{d}/n4.pcap"), gtpu_flows(f"{d}/n3.pcap")
    for r in sess:
        hit = {(u, t): n for (u, t, ip), n in flows.items() if ip == r["ue_ip"]}
        r["n3_upf"] = "+".join(sorted({u for u, _ in hit})) or ""
        r["n3_pkts"] = sum(hit.values())
        r["teid_match"] = int((r["pfcp_upf"], r["n3_teid"]) in hit) if hit and r["n3_teid"] != "" else ""
        r["agree"] = int(r["n3_upf"] == r["pfcp_upf"]) if hit else ""
    with open(f"{d}/sessions.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["n", "ue_ip", "pfcp_upf", "n3_teid", "accepted", "n3_upf", "n3_pkts", "teid_match", "agree"])
        w.writeheader()
        w.writerows(sess)
    for r in sess:
        print(f"#{r['n']:<3} {r['ue_ip']:<15} SMF->{r['pfcp_upf']}  traffic->{r['n3_upf'] or '-'} ({r['n3_pkts']} pkts)  "
              f"teid={r['n3_teid']} teid_match={r['teid_match']} accepted={r['accepted']}")
    order = [r["pfcp_upf"] for r in sess]
    switches = sum(a != b for a, b in zip(order, order[1:]))
    print(f"{len(sess)} sessions: " + ", ".join(f"{u}={order.count(u)}" for u in sorted(set(order))) +
          f"; UPF changed between consecutive sessions {switches}/{max(len(order) - 1, 0)} times; "
          f"PFCP and GTP-U agree on {sum(r['agree'] == 1 for r in sess)}/{sum(r['agree'] != '' for r in sess)} sessions with traffic")


def capture(d, n):
    if len(running()) < 2:
        raise SystemExit("start upf2 first (both UPFs must be associated, otherwise every session lands on upf1)")
    for name, (c, flt, snap) in CAPS.items():
        run("docker", "exec", "-d", P + c + "-1", "sh", "-c", f"tcpdump -i any -U -s {snap} -w /tmp/{name}.pcap {flt} 2>/tmp/{name}.err")
    time.sleep(2)
    old = {ip for _, ip in tuns()}
    ensure_ues(len(old) + n)
    time.sleep(2)
    alive([t for t in tuns() if t[1] not in old])
    time.sleep(1)
    for name, (c, *_) in CAPS.items():
        run("docker", "exec", P + c + "-1", "pkill", "-INT", "tcpdump")
    time.sleep(1)
    for name, (c, *_) in CAPS.items():
        subprocess.run(["docker", "cp", f"{P}{c}-1:/tmp/{name}.pcap", f"{d}/{name}.pcap"], check=True)


def main():
    a = sys.argv[1:]
    if a[0] == "--parse":
        return parse(a[1])
    n = int(a[a.index("--ues") + 1]) if "--ues" in a else 6
    os.makedirs(a[0], exist_ok=True)
    capture(a[0], n)
    parse(a[0])


if __name__ == "__main__":
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    main()
