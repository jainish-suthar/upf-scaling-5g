#!/bin/sh
# Phase 8: scale-down. Same ramp-up as phase 7 with the controller, then UEs leave one by one (clean deregistration).
#   fifo   : oldest UEs leave first (the UEs on upf2 joined late, so they leave last)
#   lifo   : newest UEs leave first (upf2 drains early)
#   upf2last : the UEs on upf2 leave last, the very last one LINGER s later (a long-lived session pins upf2)
#   forced : upf2last, but the controller stops upf2 DRAIN s after the load went low even if it still has sessions
# usage: sh downscale.sh [REPS] [extra ramp.py args]       results in $OUT/{fifo,lifo,upf2last,forced}_rN_* (OUT default results/downscale)
cd "$(dirname "$0")"
REPS=${1:-1}; shift 2>/dev/null
ARGS=${*:---ues 8 --rate 8 --gap 20 --hold 60 --down-gap 20 --tail 60}
Q=${UPF_QUOTA:-1000}
OUT=${OUT:-results/downscale}
DRAIN=${DRAIN:-30}
LINGER=${LINGER:-90}      # upf2last/forced: the last UE (on upf2) stays this much longer
mkdir -p "$OUT"

fresh() {
  docker compose --profile scale down -v >/dev/null 2>&1
  UPF_QUOTA=$Q UPF_N4_IPS="10.10.2.20 10.10.2.21" NUM_UES=1 docker compose up -d >/dev/null 2>&1
  for i in $(seq 1 40); do sleep 3; docker compose logs ue 2>/dev/null | grep -q "TUN interface" && return; done
  echo "UE did not come up" >&2; exit 1
}

for r in $(seq "${FIRST:-1}" "$REPS"); do
  for mode in fifo lifo upf2last forced; do
    echo "== rep $r $mode $(date +%T)"
    fresh
    case $mode in
      fifo)   python3 ramp.py $OUT/${mode}_r$r $ARGS --controller --order fifo ;;
      lifo)   python3 ramp.py $OUT/${mode}_r$r $ARGS --controller --order lifo ;;
      upf2last) python3 ramp.py $OUT/${mode}_r$r $ARGS --controller --order upf2-last --linger $LINGER ;;
      forced) python3 ramp.py $OUT/${mode}_r$r $ARGS --controller --order upf2-last --linger $LINGER --drain-timeout $DRAIN ;;
    esac
  done
done
docker compose --profile scale down -v >/dev/null 2>&1
