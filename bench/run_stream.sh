#!/bin/bash
# run_stream.sh - DDR 带宽校准工具 (STREAM + mbw)
#
# 用法: run_stream.sh [选项]
#   -a <数组大小>   STREAM 数组大小 (默认: 100000000, 约 2.4GB)
#   -t <线程数>     OpenMP 线程数 (默认: 4)
#   -c <CPU列表>    taskset CPU 绑核 (默认: 4-7, 即 A76 大核)
#   -l <标签>       测试标签 (默认: ddr_calib)
#   -m              同时运行 mbw 测试
#   -h              帮助
#
# 输出: bench/results/<标签>_<时间戳>/

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BENCH_DIR="$PROJECT_DIR/bench"

ARRAY_SIZE=100000000
N_THREADS=4
CPU_AFFINITY="4-7"
LABEL="ddr_calib"
RUN_MBW=0

usage() {
    head -20 "$0" | grep '^#' | sed 's/^# \?//'
    exit 0
}

while getopts "a:t:c:l:mh" opt; do
    case $opt in
        a) ARRAY_SIZE="$OPTARG" ;;
        t) N_THREADS="$OPTARG" ;;
        c) CPU_AFFINITY="$OPTARG" ;;
        l) LABEL="$OPTARG" ;;
        m) RUN_MBW=1 ;;
        h) usage ;;
        *) usage ;;
    esac
done

DATE_TAG=$(date +%Y%m%d_%H%M%S)
RESULT_DIR="$BENCH_DIR/results/${LABEL}_${DATE_TAG}"
mkdir -p "$RESULT_DIR"

LOG="$RESULT_DIR/stream_bench.log"

log() {
    echo "$(date +%H:%M:%S) | $*" | tee -a "$LOG"
}

log "=== DDR 带宽校准 ==="
log "结果目录: $RESULT_DIR"

# ---------- 检查内存 ----------
TOTAL_MEM_KB=$(grep MemTotal /proc/meminfo | awk '{print $2}')
TOTAL_MEM_GB=$(echo "scale=1; $TOTAL_MEM_KB / 1024 / 1024" | bc)
NEED_GB=$(echo "scale=1; $ARRAY_SIZE * 8 * 3 / 1024 / 1024 / 1024" | bc)
log "系统内存: ${TOTAL_MEM_GB} GB, STREAM 需要: ${NEED_GB} GB"

if [ "$(echo "$NEED_GB > $TOTAL_MEM_GB * 0.6" | bc -l)" -eq 1 ]; then
    log "警告: STREAM 数组可能占用过多内存"
    HALF_ARRAY=$((ARRAY_SIZE / 2))
    log "建议降低数组大小: -a $HALF_ARRAY"
    log "继续执行 (可能导致 OOM)..."
fi

# ---------- 采集系统快照 ----------
log "采集系统快照..."
bash "$SCRIPT_DIR/collect_sys.sh" "$RESULT_DIR" 3 &
COLLECT_PID=$!
sleep 1

# ---------- STREAM ----------
STREAM_SRC="$BENCH_DIR/stream.c"
STREAM_BIN="$RESULT_DIR/stream"

if [ ! -f "$STREAM_SRC" ]; then
    log "下载 STREAM 源码..."
    wget -q "https://www.cs.virginia.edu/stream/FTP/Code/stream.c" -O "$STREAM_SRC" || {
        log "wget 失败，尝试 curl..."
        curl -sL "https://www.cs.virginia.edu/stream/FTP/Code/stream.c" -o "$STREAM_SRC" || {
            log "无法下载 stream.c，请手动下载到 $STREAM_SRC"
            kill "$COLLECT_PID" 2>/dev/null || true
            exit 1
        }
    }
fi

log "编译 STREAM (ARRAY_SIZE=$ARRAY_SIZE, OMP_THREADS=$N_THREADS)..."
gcc -O3 -march=armv8-a -mtune=cortex-a76 \
    -fopenmp \
    -DSTREAM_ARRAY_SIZE="$ARRAY_SIZE" \
    -DNTIMES=20 \
    -o "$STREAM_BIN" "$STREAM_SRC"

log ""
log "--- 单线程 STREAM ---"
OMP_NUM_THREADS=1 "$STREAM_BIN" 2>&1 | tee "$RESULT_DIR/stream_1t.txt"

log ""
log "--- ${N_THREADS} 线程 STREAM (绑核 $CPU_AFFINITY) ---"
OMP_NUM_THREADS="$N_THREADS" OMP_PROC_BIND=close \
    taskset -c "$CPU_AFFINITY" \
    "$STREAM_BIN" 2>&1 | tee "$RESULT_DIR/stream_${N_THREADS}t.txt"

# ---------- 提取 STREAM 结果 ----------
STREAM_CSV="$RESULT_DIR/stream_results.csv"
{
    echo "test,n_threads,copy_mb_s,scale_mb_s,add_mb_s,triad_mb_s"
    
    for TF in "$RESULT_DIR"/stream_*t.txt; do
        NT=$(basename "$TF" | sed 's/stream_//;s/t.txt//')
        COPY=$(grep "^Copy:" "$TF" | awk '{print $2}')
        SCALE=$(grep "^Scale:" "$TF" | awk '{print $2}')
        ADD=$(grep "^Add:" "$TF" | awk '{print $2}')
        TRIAD=$(grep "^Triad:" "$TF" | awk '{print $2}')
        echo "stream,$NT,$COPY,$SCALE,$ADD,$TRIAD"
    done
} > "$STREAM_CSV"

# ---------- mbw ----------
if [ $RUN_MBW -eq 1 ]; then
    MBW_BIN=$(which mbw 2>/dev/null || true)
    if [ -z "$MBW_BIN" ]; then
        log "mbw 未安装，尝试编译..."
        MBW_DIR="$BENCH_DIR/mbw"
        if [ ! -d "$MBW_DIR" ]; then
            git clone --depth 1 https://github.com/raas/mbw.git "$MBW_DIR" 2>/dev/null || {
                log "无法克隆 mbw，跳过"
                MBW_BIN=""
            }
        fi
        if [ -d "$MBW_DIR" ]; then
            make -C "$MBW_DIR" 2>/dev/null
            MBW_BIN="$MBW_DIR/mbw"
        fi
    fi

    if [ -n "$MBW_BIN" ] && [ -x "$MBW_BIN" ]; then
        log ""
        log "--- mbw 测试 ---"
        MBW_SIZE=256
        "$MBW_BIN" "$MBW_SIZE" 2>&1 | tee "$RESULT_DIR/mbw_${MBW_SIZE}.txt" || true
        
        MBW_CSV="$RESULT_DIR/mbw_results.csv"
        echo "test,size_mb,avg_rate_mb_s" > "$MBW_CSV"
        AVG=$(grep "AVG" "$RESULT_DIR/mbw_${MBW_SIZE}.txt" | awk '{print $2}' || echo "0")
        echo "mbw,$MBW_SIZE,$AVG" >> "$MBW_CSV"
    else
        log "mbw 不可用，跳过"
    fi
fi

# ---------- 停止采集 ----------
kill "$COLLECT_PID" 2>/dev/null || true
wait "$COLLECT_PID" 2>/dev/null || true

# ---------- 校准天花板 ----------
log ""
log "=== DDR 带宽校准结果 ==="
cat "$STREAM_CSV" | tee -a "$LOG"

TRIAD_MT=$(grep "stream,$N_THREADS" "$STREAM_CSV" | cut -d',' -f6)
if [ -n "$TRIAD_MT" ]; then
    TRIAD_GB=$(echo "scale=2; $TRIAD_MT / 1024" | bc)
    log ""
    log "多线程 Triad 带宽: ${TRIAD_GB} GB/s"
    
    for MODEL_SIZE in 0.9 1.8 2.2 3.9 5.0; do
        CEILING=$(echo "scale=1; $TRIAD_GB / $MODEL_SIZE * 0.85" | bc)
        log "  模型 ${MODEL_SIZE} GB: 理论上限 ~${CEILING} tok/s"
    done
fi

log ""
log "结果保存到: $RESULT_DIR"
