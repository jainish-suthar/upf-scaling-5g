#!/bin/sh
# Final measurement session for the report: everything on one host state, in one go (~3.5 h).
#   1. host state  -> $OUT/host.txt
#   2. phase 3     -> $OUT/threshold.csv (+ plot): knee of one UPF at UPF_QUOTA, 4 shuffled runs per load
#   3. per-UE rate -> picked from the knee so 8 UEs offer ~1.4x one UPF's capacity (above one UPF, below two)
#   4. phases 7+8  -> $OUT/ramp/<variant>_rN_*: REPS repetitions, interleaved, fresh stack each run. Every run ramps up,
#                     holds, then UEs leave one by one, so the same runs give the scale-up and the scale-down results:
#        single   : one UPF only                          static   : both UPFs from the start
#        fifo     : controller, oldest UEs leave first     lifo     : controller, newest leave first (upf2 empties early)
#        upf2last : controller, upf2's UEs leave last, the last one LINGER s later (long-lived session pins upf2)
#        forced   : upf2last + --drain-timeout DRAIN (stop upf2 anyway, cutting its session)
#        lifoearly: lifo + --empty-down (stop upf2 once empty while upf1 alone is under the scale-up limit)
#   5. tables      -> $OUT/summary.md, plots for repetition 2
# usage: systemd-inhibit --what=sleep:idle:handle-lid-switch sh final.sh
#        env: REPS=3 OUT=results/final UPF_QUOTA=1000 DRAIN=30 LINGER=90 RATE=<force a per-UE rate>
#             FITS=45 (Mbit/s at which upf1 hits the 80% scale-up limit in the sweep; re-read it from threshold.csv)
#             SKIP_SWEEP=1 (reuse $OUT/threshold.csv)   FIRST=2 (resume phases 7+8 at repetition 2)
cd "$(dirname "$0")"
REPS=${REPS:-3}
OUT=${OUT:-results/final}
Q=${UPF_QUOTA:-1000}
DRAIN=${DRAIN:-30}
LINGER=${LINGER:-90}
VARIANTS="single static fifo lifo upf2last forced lifoearly"
mkdir -p "$OUT/ramp"
export PYTHONNOUSERSITE=1          # system matplotlib/numpy (a user-installed NumPy 2 breaks matplotlib)
log() { echo "== $(date +%T) $*" | tee -a "$OUT/progress.log"; }

# 1. host state
{
  date; uname -r; nproc; free -h | head -2
  echo "governor: $(cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_governor 2>/dev/null)"
  echo "platform profile: $(cat /sys/firmware/acpi/platform_profile 2>/dev/null)"
  echo "AC online: $(cat /sys/class/power_supply/*/online 2>/dev/null | tr '\n' ' ')"
  echo "load: $(cat /proc/loadavg)"
  docker version --format 'docker {{.Server.Version}}'
  echo "top CPU users:"; ps -eo pcpu,comm --sort=-pcpu | head -8
} > "$OUT/host.txt" 2>&1
log "host state written; load $(cut -d' ' -f1-3 /proc/loadavg)"

fresh() {    # $1 = UPF_N4_IPS, $2 = extra compose flags
  docker compose --profile scale down -v >/dev/null 2>&1
  UPF_QUOTA=$Q UPF_N4_IPS="$1" NUM_UES=1 docker compose $2 up -d >/dev/null 2>&1
  for i in $(seq 1 40); do sleep 3; docker compose logs ue 2>/dev/null | grep -q "TUN interface" && return 0; done
  log "UE did not come up"; return 1
}

# 2. phase 3: where one UPF degrades (10..120 Mbit/s offered by 4 UEs)
if [ -z "$SKIP_SWEEP" ]; then
  log "phase 3 sweep"
  fresh "10.10.2.20" "" || exit 1
  python3 sweep.py "$OUT/threshold.csv" --reps 4 --shuffle 1 4x2.5 4x5 4x7.5 4x10 4x12.5 4x15 4x17.5 4x20 4x25 4x30 >> "$OUT/progress.log" 2>&1
  python3 plot.py sweep "$OUT/threshold.csv" >/dev/null 2>&1
fi

# 3. per-UE rate from the knee: one UPF's capacity = highest median delivered throughput over the valid runs
if [ -z "$RATE" ]; then
  RATE=$(python3 - "$OUT/threshold.csv" <<'EOF'
import csv, statistics, sys
rows = [r for r in csv.DictReader(open(sys.argv[1])) if r.get("valid", "1") == "1"]
by = {}
for r in rows:
    by.setdefault(float(r["offered_mbps"]), []).append(float(r["throughput_mbps"]))
cap = max(statistics.median(v) for v in by.values())
print(min(16.0, max(6.0, round(1.4 * cap / 8 * 2) / 2)))
EOF
)
fi
log "per-UE rate $RATE Mbit/s (8 UEs = $(python3 -c "print(8*$RATE)") Mbit/s offered)"
ARGS="--ues 8 --rate $RATE --gap 20 --hold 60 --down-gap 20 --tail 60"

# 4. phases 7+8
for r in $(seq "${FIRST:-1}" "$REPS"); do
  for v in $VARIANTS; do
    log "rep $r $v"
    p="$OUT/ramp/${v}_r$r"
    case $v in
      single)    fresh "10.10.2.20" "" ;;
      static)    fresh "10.10.2.20 10.10.2.21" "--profile scale" ;;
      *)         fresh "10.10.2.20 10.10.2.21" "" ;;
    esac || continue
    case $v in
      single|static) python3 ramp.py "$p" $ARGS ;;
      fifo)      python3 ramp.py "$p" $ARGS --controller --order fifo ;;
      lifo)      python3 ramp.py "$p" $ARGS --controller --order lifo ;;
      upf2last)  python3 ramp.py "$p" $ARGS --controller --order upf2-last --linger "$LINGER" ;;
      forced)    python3 ramp.py "$p" $ARGS --controller --order upf2-last --linger "$LINGER" --drain-timeout "$DRAIN" ;;
      lifoearly) python3 ramp.py "$p" $ARGS --controller --order lifo --empty-down ;;
    esac > "$p.log" 2>&1
    tail -2 "$p.log" >> "$OUT/progress.log"
  done
done
docker compose --profile scale down -v >/dev/null 2>&1

# 5. tables and plots
log "summaries"
R=$OUT/ramp
{
  echo "# Final results ($(date +%F), UPF_QUOTA=$Q, per-UE rate $RATE Mbit/s, $REPS repetitions)"; echo
  echo "## Scale-up: one UPF vs controller vs two UPFs (steady state before the first UE leaves)"; echo
  python3 summarize.py $R/single_r*_ues.csv -- $R/fifo_r*_ues.csv -- $R/static_r*_ues.csv; echo
  echo "## Scale-down"; echo
  python3 summarize.py --down --fits "${FITS:-45}" $R/fifo_r*_ues.csv $R/lifo_r*_ues.csv $R/lifoearly_r*_ues.csv $R/upf2last_r*_ues.csv $R/forced_r*_ues.csv
} > "$OUT/summary.md" 2>&1
python3 plot.py ramp $R/single_r2 $R/fifo_r2 $R/static_r2 >/dev/null 2>&1
python3 plot.py ramp $R/lifo_r2 $R/lifoearly_r2 >/dev/null 2>&1
python3 plot.py ramp $R/upf2last_r2 $R/forced_r2 >/dev/null 2>&1
log "done: $OUT/summary.md"
