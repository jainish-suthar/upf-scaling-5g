cd /etc/open5gs
sed -i "/^  pfcp:/,/^  gtpu:/ s/127.0.0.7/$N4_IP/; /^  gtpu:/,/^  session:/ s/127.0.0.7/$N3_IP/; /^  metrics:/,/port/ s/127.0.0.7/0.0.0.0/" upf.yaml
ip tuntap add name ogstun mode tun
ip addr add 10.45.0.1/16 dev ogstun
ip link set ogstun up
# UE traffic leaves N6 behind this UPF's own N6 address, so the data network can reply without per-UPF routes
N6_DEV=$(ip -o -4 addr | awk -v ip="$N6_IP/" 'index($4, ip) {print $2}')
iptables -t nat -A POSTROUTING -s 10.45.0.0/16 -o $N6_DEV -j MASQUERADE
mkdir -p /var/log/open5gs
open5gs-upfd -c /etc/open5gs/upf.yaml &
sleep 2; tail -F /var/log/open5gs/upf.log
