cd /etc/open5gs
sed -i "/^  ngap:/,/address/ s/127.0.0.5/$N2_IP/" amf.yaml
set -- $UPF_N4_IPS
sed -i "/^  pfcp:/,/^  gtpc:/ { /server:/,/client:/ s/127.0.0.4/$N4_IP/; s/127.0.0.7/$1/ }" smf.yaml
prev=$1; shift
for ip in "$@"; do sed -i "/- address: $prev\$/a\\        - address: $ip" smf.yaml; prev=$ip; done
sed -i "s#mongodb://localhost#mongodb://mongo#" udr.yaml pcf.yaml
# fail loudly if a sed above matched nothing (e.g. default config layout changed in another Open5GS version)
check() { grep -q "$2" "$1" || { echo "config edit failed: '$2' not in $1" >&2; exit 1; }; }
check amf.yaml "address: $N2_IP"
check smf.yaml "address: $N4_IP"
for ip in $UPF_N4_IPS; do check smf.yaml "address: $ip"; done
check udr.yaml "mongodb://mongo"
mkdir -p /var/log/open5gs
for nf in nrf scp udr udm ausf pcf nssf smf amf; do
  open5gs-${nf}d -c /etc/open5gs/$nf.yaml &
  sleep 1
done
sleep 2; tail -F /var/log/open5gs/*.log
