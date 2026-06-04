#!/bin/bash
# run_bench.sh - llama.cpp firstbird 基准测试运行器
#
# 用法: run_bench.sh [选项]
#   -m <模型路径>     模型文件 (默认: models/llama-7B/ggml-model.bin)
#   -p <prompt>       输入提示 (默认: "Hello, how are you")
#   -n <生成数>       生成 token 数 (默认: 64)
#   -t <线程数>       推理线程数 (默认: 4)
#   -s <种子>         随机种子 (默认: 42)
#   -l <标签>         本次测试标签，如 "baseline", "sdot_v1", "prefetch_v2"
#   -c <采集间隔>     系统指标采集间隔秒 (默认: 2)
#   -P                启用 perf stat 采集 (需要 perf 工具)
#   -h                帮助
#
# 输出目录结构: bench/results/<标签>_<时间戳>/
#   bench.log          - 完整运行日志
#   llama_output.txt   - llama.cpp 原始输出
#   timing.csv         - 关键时序数据 (CSV)
#   perf_stat.log      - perf stat 原始输出 (如启用)
#   sysinfo_*.txt      - 系统快照 (由 collect_sys 生成)
#   cpufreq_*.log      - CPU 频率时序 (CSV)
#   thermal_*.log      - 温度时序 (CSV)
#   top_snapshot_*.log - top 快照
#   vmstat_*.log       - vmstat 时序

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BENCH_DIR="$PROJECT_DIR/bench"

# 默认参数
MODEL="models/llama-7B/ggml-model.bin"
PROMPT="Hello, how are you"
N_PREDICT=64
N_THREADS=4
SEED=42
LABEL=""
COLLECT_INTERVAL=2
USE_PERF=0

usage() {
    head -20 "$0" | grep '^#' | sed 's/^# \?//'
    exit 0
}

while getopts "m:p:n:t:s:l:c:Ph" opt; do
    case $opt in
        m) MODEL="$OPTARG" ;;
        p) PROMPT="$OPTARG" ;;
        n) N_PREDICT="$OPTARG" ;;
        t) N_THREADS="$OPTARG" ;;
        s) SEED="$OPTARG" ;;
        l) LABEL="$OPTARG" ;;
        c) COLLECT_INTERVAL="$OPTARG" ;;
        P) USE_PERF=1 ;;
        h) usage ;;
        *) usage ;;
    esac
done

if [ -z "$LABEL" ]; then
    LABEL="unnamed"
fi

# 构建结果目录
DATE_TAG=$(date +%Y%m%d_%H%M%S)
RESULT_DIR="$BENCH_DIR/results/${LABEL}_${DATE_TAG}"
mkdir -p "$RESULT_DIR"

LOG="$RESULT_DIR/bench.log"

log() {
    echo "$(date +%H:%M:%S) | $*" | tee -a "$LOG"
}

# ---------- 预检查 ----------
log "=== llama.cpp firstbird benchmark runner ==="
log "结果目录: $RESULT_DIR"

BIN="$PROJECT_DIR/main"
if [ ! -x "$BIN" ]; then
    log "错误: 找不到可执行文件 $BIN"
    log "请先编译: cd $PROJECT_DIR && make"
    exit 1
fi

if [ ! -f "$MODEL" ]; then
    log "警告: 模型文件 $MODEL 不存在"
    log "推理可能会失败，继续执行..."
fi

# git 信息
GIT_INFO="not_a_git_repo"
if git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null; then
    GIT_INFO=$(git -C "$PROJECT_DIR" rev-parse --short HEAD)
    GIT_DIRTY=""
    if ! git -C "$PROJECT_DIR" diff --quiet 2>/dev/null; then
        GIT_DIRTY="+dirty"
    fi
    GIT_INFO="${GIT_INFO}${GIT_DIRTY}"
fi

# ---------- 记录测试配置 ----------
log "测试配置:"
log "  标签:     $LABEL"
log "  模型:     $MODEL"
log "  Prompt:   $PROMPT"
log "  生成数:   $N_PREDICT"
log "  线程数:   $N_THREADS"
log "  种子:     $SEED"
log "  perf:     $([ $USE_PERF -eq 1 ] && echo '启用' || echo '禁用')"
log "  Git:      $GIT_INFO"
log ""

# 保存配置到 CSV (方便后续批量对比)
CONFIG_CSV="$RESULT_DIR/config.csv"
{
    echo "label,timestamp,git,model,n_predict,n_threads,seed,prompt,use_perf"
    echo "$LABEL,$DATE_TAG,$GIT_INFO,$MODEL,$N_PREDICT,$N_THREADS,$SEED,\"$PROMPT\",$USE_PERF"
} > "$CONFIG_CSV"

# ---------- 启动系统指标采集 ----------
log "启动系统指标采集 (间隔 ${COLLECT_INTERVAL}s)..."
bash "$SCRIPT_DIR/collect_sys.sh" "$RESULT_DIR" "$COLLECT_INTERVAL" &
COLLECT_PID=$!
sleep 1

# ---------- 运行推理 ----------
log "开始推理..."
log "---"

LLAMA_OUTPUT="$RESULT_DIR/llama_output.txt"

if [ $USE_PERF -eq 1 ]; then
    PERF_OUTPUT="$RESULT_DIR/perf_stat.log"
    log "perf stat 采集 -> $PERF_OUTPUT"
    perf stat -d \
        -e cycles,instructions,cache-references,cache-misses,L1-dcache-loads,L1-dcache-load-misses,LLC-loads,LLC-load-misses,dTLB-loads,dTLB-load-misses \
        -o "$PERF_OUTPUT" \
        "$BIN" -m "$MODEL" -p "$PROMPT" -n "$N_PREDICT" -t "$N_THREADS" -s "$SEED" \
        2>&1 | tee "$LLAMA_OUTPUT"
else
    "$BIN" -m "$MODEL" -p "$PROMPT" -n "$N_PREDICT" -t "$N_THREADS" -s "$SEED" \
        2>&1 | tee "$LLAMA_OUTPUT"
fi

log "---"
log "推理完成"

# ---------- 停止系统指标采集 ----------
log "停止系统指标采集..."
kill "$COLLECT_PID" 2>/dev/null || true
wait "$COLLECT_PID" 2>/dev/null || true
sleep 1

# ---------- 提取时序数据 ----------
log "提取时序数据..."

TIMING_CSV="$RESULT_DIR/timing.csv"
{
    echo "metric,value,unit"
    # 从 llama_output.txt 提取关键时序
    grep "mem per token" "$LLAMA_OUTPUT" | sed 's/.*= //' | awk '{print "mem_per_token,"$1",bytes"}' || true
    grep "load time" "$LLAMA_OUTPUT" | sed 's/.*= //' | awk '{print "load_time,"$1",ms"}' || true
    grep "sample time" "$LLAMA_OUTPUT" | sed 's/.*= //' | awk -F'/' '{print "sample_time,"$1",ms"}' || true
    grep "predict time" "$LLAMA_OUTPUT" | sed 's/.*= //' | awk -F'/' '{print "predict_time,"$1",ms"}' || true

    # 提取 per-token 延迟和 tok/s
    PER_TOKEN=$(grep "predict time" "$LLAMA_OUTPUT" | grep -oP '[\d.]+\s+ms per token' | grep -oP '[\d.]+' || echo "")
    if [ -n "$PER_TOKEN" ]; then
        TPS=$(echo "scale=2; 1000 / $PER_TOKEN" | bc 2>/dev/null || echo "0")
        echo "per_token_ms,$PER_TOKEN,ms"
        echo "tokens_per_second,$TPS,tok/s"
    fi

    # perf 数据提取 (如启用)
    if [ $USE_PERF -eq 1 ] && [ -f "$RESULT_DIR/perf_stat.log" ]; then
        grep "cache-misses" "$RESULT_DIR/perf_stat.log" | head -1 | awk '{print "perf_cache_misses,"$1",count"}' || true
        grep "LLC-load-misses" "$RESULT_DIR/perf_stat.log" | head -1 | awk '{print "perf_llc_load_misses,"$1",count"}' || true
        grep "L1-dcache-load-misses" "$RESULT_DIR/perf_stat.log" | head -1 | awk '{print "perf_l1dcache_load_misses,"$1",count"}' || true
    fi
} > "$TIMING_CSV"

# ---------- 汇总 ----------
log ""
log "=== 结果摘要 ==="
log "结果目录: $RESULT_DIR"
cat "$TIMING_CSV" | tee -a "$LOG"
log ""
log "文件列表:"
ls -la "$RESULT_DIR" | tail -n +2 | while read line; do
    log "  $line"
done
log ""
log "完成后可用 bench/compare.sh 对比不同测试结果"
