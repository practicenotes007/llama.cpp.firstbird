#!/bin/bash
# diag_bandwidth_gap.sh - 小模型带宽利用率诊断
# 
# 测试矩阵: governor(ondemand/performance) × threads(1/4)
# 输出: bench/results/diag_bw_gap_<ts>/report.txt

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BENCH_DIR="$PROJECT_DIR/bench"
RESULT_DIR="$BENCH_DIR/results/diag_bw_gap_$(date +%Y%m%d_%H%M%S)"
mkdir -p "$RESULT_DIR"

MODEL="$PROJECT_DIR/models/qwen2.5-1.5b-instruct-q4_k_m.gguf"
TZONE="/sys/class/thermal/thermal_zone1/temp"
FREQ="/sys/devices/system/cpu/cpu4/cpufreq/scaling_cur_freq"
GOVERNOR="/sys/devices/system/cpu/cpu4/cpufreq/scaling_governor"
N_TOKENS=64
REPORT="$RESULT_DIR/report.txt"

log() { echo "$(date +%H:%M:%S) | $*" | tee -a "$REPORT"; }

# 保存当前 governor 状态
CUR_GOV="$(cat $GOVERNOR)"

cleanup() {
    log "恢复 governor: $CUR_GOV"
    echo "$CUR_GOV" | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null 2>&1
}
trap cleanup EXIT

# 写 governor（所有核）
set_gov() {
    echo "$1" | sudo tee /sys/devices/system/cpu/cpu*/cpufreq/scaling_governor >/dev/null 2>&1
    sleep 1
}

# ---------- Header ----------
cat > "$REPORT" <<EOF
=== 小模型带宽利用率诊断报告 ===
日期: $(date)
模型: Qwen2.5-1.5B Q4_K_M (1.04 GB)
DDR: 实测 21.46 GB/s
Q4_K super-block=256
基线: 14.6 tok/s, 带宽利用率 71%
目标: 诊断 18% 带宽缺口根因

EOF

# ---------- 测试函数 ----------
run_test() {
    local label="$1"
    local gov="$2"
    local th="$3"
    
    log "--- $label ($gov, $th threads) ---"
    set_gov "$gov"
    
    # 冷却
    sleep 15
    
    t_start=$(cat $TZONE)
    f_start=$(cat $FREQ)
    
    output=$("$PROJECT_DIR/main" -m "$MODEL" -p "Hello" -n "$N_TOKENS" -t "$th" 2>&1)
    
    t_end=$(cat $TZONE)
    f_end=$(cat $FREQ)
    
    tok_line=$(echo "$output" | grep "eval time" || echo "")
    if [ -z "$tok_line" ]; then
        log "  ERROR: no eval time line"
        echo "$output" >> "$RESULT_DIR/error_${label}.log"
        return
    fi
    
    # 提取数据
    eval_ms=$(echo "$tok_line" | awk '/eval time/{print $4}')
    tok_ms=$(echo "$tok_line" | awk '/eval time/{print $6}')
    
    # 计算 tok/s
    tok_per_s=$(echo "scale=2; 1000 / $tok_ms" | bc)
    bw_util=$(echo "scale=1; $tok_per_s * 1.04 / 21.46 * 100" | bc)
    
    log "  result: ${eval_ms}ms total / ${tok_ms}ms per token / ${tok_per_s} tok/s"
    log "  freq:   ${f_start} → ${f_end} Hz (governor=$gov)"
    log "  temp:   ${t_start} → ${t_end}"
    log "  bw_util: ${bw_util}%"
    log ""
    
    echo "$label,$gov,$th,$eval_ms,$tok_ms,$tok_per_s,$f_start,$f_end,$t_start,$t_end,$bw_util" >> "$RESULT_DIR/data.csv"
}

# ---------- CSV header ----------
echo "label,governor,threads,eval_ms,tok_ms,tok_s,freq_start,freq_end,temp_start,temp_end,bw_util_pct" > "$RESULT_DIR/data.csv"

# ---------- 测试矩阵 ----------
log "=== 开始测试矩阵 ==="
log ""

# 1. ondemand, 4 threads (基线)
run_test "baseline" "ondemand" 4

# 2. ondemand, 1 thread
run_test "t1_ondemand" "ondemand" 1

# 3. performance, 4 threads
run_test "perf_t4" "performance" 4

# 4. performance, 1 thread
run_test "perf_t1" "performance" 1

# ---------- Perf stat (可选, 如果安装了 perf) ----------
if command -v perf &>/dev/null; then
    log "=== perf stat (performance, 4t) ==="
    set_gov "performance"
    sleep 5
    perf stat -e cycles,instructions,cache-misses,cache-references,bus-cycles \
        "$PROJECT_DIR/main" -m "$MODEL" -p "Hello" -n "$N_TOKENS" -t 4 2>&1 | tee -a "$REPORT"
fi

# ---------- 汇总 ----------
log ""
log "=== 汇总 ==="
cat "$RESULT_DIR/data.csv" | column -t -s,
log ""
log "文件: $RESULT_DIR"
