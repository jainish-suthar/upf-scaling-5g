#!/bin/sh
# Phase 7: the same ramp under three set-ups, REPS times each, interleaved, fresh stack every run.
#   single : one UPF only               scaled : both listed, upf2 started by controller.py               static : both UPFs up from the start
# usage: sh compare.sh [REPS] [extra ramp.py args]          results in $OUT/{single,scaled,static}_rN_* (OUT default results/compare)
cd "$(dirname "$0")"
REPS=${1:-3}; shift 2>/dev/null
ARGS=${*:---ues 8 --rate 15 --gap 20 --hold 60}
Q=${UPF_QUOTA:-1000}
OUT=${OUT:-results/compare}
mkdir -p "$OUT"

fresh() {    # $1 = UPF_N4_IPS, $2 = extra compose flags
  docker compose --profile scale down -v >/dev/null 2>&1
  UPF_QUOTA=$Q UPF_N4_IPS="$1" NUM_UES=1 docker compose $2 up -d >/dev/null 2>&1
  for i in $(seq 1 40); do sleep 3; docker compose logs ue 2>/dev/null | grep -q "TUN interface" && return; done
  echo "UE did not come up" >&2; exit 1
}

for r in $(seq "${FIRST:-1}" "$REPS"); do   # FIRST=2 resumes at a later repetition
  for mode in single scaled static; do
    echo "== rep $r $mode $(date +%T)"
    case $mode in
      single) fresh "10.10.2.20" "";                     python3 ramp.py $OUT/${mode}_r$r $ARGS ;;
      scaled) fresh "10.10.2.20 10.10.2.21" "";          python3 ramp.py $OUT/${mode}_r$r $ARGS --controller ;;
      static) fresh "10.10.2.20 10.10.2.21" "--profile scale"; python3 ramp.py $OUT/${mode}_r$r $ARGS ;;
    esac
  done
done
docker compose --profile scale down -v >/dev/null 2>&1
python3 summarize.py "$OUT"/single_r*_ues.csv -- "$OUT"/scaled_r*_ues.csv -- "$OUT"/static_r*_ues.csv
