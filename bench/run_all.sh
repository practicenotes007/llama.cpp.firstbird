#!/bin/bash
# run_all.sh - 完整测试流水线：修改 -> 测试 -> 基准 -> 对比 -> 可视化
#
# 在每次代码修改后，运行此脚本即可得到完整的验证报告。
#
# 用法: run_all.sh [选项]
#   -m <模型路径>    模型文件 (默认: models/llama-7B/ggml-model.bin)
#   -t <线程数>      推理线程数 (默认: 4)
#   -n <生成数>      生成 token 数 (默认: 64)
#   -l <标签>        本次测试标签 (默认: auto_<时间戳>)
#   -s <种子>        随机种子 (默认: 42)
#   -P               启用 perf stat
#   -D               跳过 DDR 带宽校准
#   -h               帮助
#
# 输出: bench/results/<标签>_<时间戳>/
#       含完整时序、系统指标、正确性报告、对比图

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

# 从命令行 args 构建 run_bench.sh 的参数
BENCH_ARGS=()
RUN_DDR=1
LABEL=""

while getopts "m:t:n:l:s:PDh" opt; do
    case $opt in
        m) BENCH_ARGS+=(-m "$OPTARG") ;;
        t) BENCH_ARGS+=(-t "$OPTARG") ;;
        n) BENCH_ARGS+=(-n "$OPTARG") ;;
        l) LABEL="$OPTARG" ;;
        s) BENCH_ARGS+=(-s "$OPTARG") ;;
        P) BENCH_ARGS+=(-P) ;;
        D) RUN_DDR=0 ;;
        h)
            head -30 "$0" | grep '^#' | sed 's/^# \?//'
            exit 0
            ;;
        *) exit 1 ;;
    esac
done

if [ -z "$LABEL" ]; then
    LABEL="auto_$(date +%m%d_%H%M)"
fi

DATE_TAG=$(date +%Y%m%d_%H%M%S)
RESULT_DIR="$SCRIPT_DIR/results/${LABEL}_${DATE_TAG}"
mkdir -p "$RESULT_DIR"

LOG="$RESULT_DIR/pipeline.log"

log() {
    echo "$(date +%H:%M:%S) | $*" | tee -a "$LOG"
}

log "============================================"
log "  llama.cpp firstbird 完整测试流水线"
log "============================================"
log ""

# 检查当前 git 状态
if git -C "$PROJECT_DIR" rev-parse --short HEAD 2>/dev/null; then
    GIT_HASH=$(git -C "$PROJECT_DIR" rev-parse --short HEAD)
    GIT_DIRTY=""
    if ! git -C "$PROJECT_DIR" diff --quiet 2>/dev/null; then
        GIT_DIRTY=" (有未提交修改)"
    fi
    log "Git: $GIT_HASH$GIT_DIRTY"
    git -C "$PROJECT_DIR" diff --stat 2>/dev/null >> "$LOG" || true
    log ""
fi

# ---------- Step 1: 编译 ----------
log "=== Step 1: 编译 ==="
log "清理并编译..."
make -C "$PROJECT_DIR" clean 2>&1 | tail -5 | while read line; do log "  $line"; done
make -C "$PROJECT_DIR" test_kernels main 2>&1 | while read line; do log "  $line"; done
log "编译完成"
log ""

# ---------- Step 2: 内核正确性测试 ----------
log "=== Step 2: 内核正确性测试 ==="
TEST_LOG="$RESULT_DIR/correctness.log"
"$PROJECT_DIR/test_kernels" --verbose 2>&1 | tee "$TEST_LOG" || {
    log "!!! 正确性测试失败，停止流水线 !!!"
    exit 1
}
log "正确性测试通过"
log ""

# ---------- Step 3: 推理基准测试 ----------
log "=== Step 3: 推理基准测试 ==="
log "运行: $PROJECT_DIR/bench/run_bench.sh -l \"$LABEL\" ${BENCH_ARGS[*]}"
bash "$SCRIPT_DIR/run_bench.sh" -l "$LABEL" "${BENCH_ARGS[@]}" 2>&1 | tee -a "$LOG" || {
    log "!!! 推理基准测试失败 !!!"
    exit 1
}

# 定位结果目录
BENCH_RESULT=$(ls -1dt "$SCRIPT_DIR/results/${LABEL}"_* 2>/dev/null | head -1)
if [ -n "$BENCH_RESULT" ]; then
    log "推理基准结果: $BENCH_RESULT"
    log ""
fi

# ---------- Step 4: DDR 带宽校准 (可选) ----------
if [ $RUN_DDR -eq 1 ]; then
    log "=== Step 4: DDR 带宽校准 ==="
    bash "$SCRIPT_DIR/run_stream.sh" -l "ddr_${LABEL}" -m 2>&1 | tee -a "$LOG" || {
        log "DDR 校准失败（非致命）"
    }
fi

# ---------- Step 5: 对比与可视化 ----------
log "=== Step 5: 对比与可视化 ==="

# 找到最近的两次测试（包括本次）用于对比
RECENT=($(ls -1dt "$SCRIPT_DIR/results/"*/ | head -3))
if [ ${#RECENT[@]} -ge 2 ]; then
    log "对比: $(basename ${RECENT[1]}) vs $(basename ${RECENT[0]})"
    bash "$SCRIPT_DIR/compare.sh" "${RECENT[1]}" "${RECENT[0]}" 2>&1 | tee -a "$LOG" || true
    log ""
fi

# 绘图
if command -v python3 &>/dev/null; then
    if python3 -c "import matplotlib" 2>/dev/null; then
        log "绘制系统指标图..."
        python3 "$SCRIPT_DIR/plot_results.py" --single "$BENCH_RESULT" 2>&1 | tee -a "$LOG" || true
        log "绘制对比图..."
        python3 "$SCRIPT_DIR/plot_results.py" --last 3 2>&1 | tee -a "$LOG" || true
        log ""
    else
        log "matplotlib 未安装，跳过绘图 (pip install matplotlib)"
    fi
fi

# ---------- 汇总 ----------
log "============================================"
log "  测试流水线完成"
log "============================================"
log ""

if [ -n "$BENCH_RESULT" ]; then
    log "结果目录: $BENCH_RESULT"
    log "文件列表:"
    ls -la "$BENCH_RESULT" >> "$LOG"
    for f in "$BENCH_RESULT"/*; do
        log "  $(basename "$f")"
    done
    log ""

    # 提取核心指标
    TIMING="$BENCH_RESULT/timing.csv"
    if [ -f "$TIMING" ]; then
        log "核心指标:"
        grep -E "tokens_per_second|per_token_ms|predict_time" "$TIMING" | while read line; do
            log "  $line"
        done
    fi
fi

log "日志文件: $LOG"
