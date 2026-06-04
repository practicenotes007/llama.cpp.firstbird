#!/bin/bash
# compare.sh - 基准测试结果对比工具
#
# 用法: compare.sh <结果目录1> <结果目录2> [结果目录3...]
#   或:  compare.sh --last <N>    对比最近 N 次测试
#   或:  compare.sh --label <标签前缀>  对比所有匹配标签的结果
#
# 示例:
#   compare.sh results/baseline_20260601_120000 results/sdot_v1_20260601_130000
#   compare.sh --last 3
#   compare.sh --label baseline

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
BENCH_DIR="$SCRIPT_DIR"

MODE="dirs"
DIRS=()

if [ $# -eq 0 ]; then
    echo "用法: compare.sh <结果目录1> <结果目录2> [...]"
    echo "      compare.sh --last <N>"
    echo "      compare.sh --label <标签前缀>"
    exit 1
fi

if [ "$1" = "--last" ]; then
    N="${2:-3}"
    MODE="last"
elif [ "$1" = "--label" ]; then
    LABEL_PREFIX="${2:-}"
    MODE="label"
else
    MODE="dirs"
    DIRS=("$@")
fi

RESULT_BASE="$BENCH_DIR/results"

if [ ! -d "$RESULT_BASE" ]; then
    echo "没有测试结果目录: $RESULT_BASE"
    exit 1
fi

# 收集要对比的目录
COMPARE_DIRS=()

case $MODE in
    last)
        COMPARE_DIRS=($(ls -1dt "$RESULT_BASE"/*/ | head -"$N"))
        ;;
    label)
        for d in "$RESULT_BASE"/*; do
            basename=$(basename "$d")
            if [[ "$basename" == ${LABEL_PREFIX}* ]]; then
                COMPARE_DIRS+=("$d")
            fi
        done
        # 按时间排序
        IFS=$'\n' COMPARE_DIRS=($(printf '%s\n' "${COMPARE_DIRS[@]}" | sort))
        ;;
    dirs)
        for d in "${DIRS[@]}"; do
            if [ -d "$d" ]; then
                COMPARE_DIRS+=("$d")
            else
                # 尝试作为相对路径
                FULL="$RESULT_BASE/$d"
                if [ -d "$FULL" ]; then
                    COMPARE_DIRS+=("$FULL")
                else
                    echo "警告: 目录不存在 $d"
                fi
            fi
        done
        ;;
esac

if [ ${#COMPARE_DIRS[@]} -eq 0 ]; then
    echo "没有找到可对比的结果目录"
    exit 1
fi

echo "============================================"
echo "  基准测试结果对比"
echo "============================================"
echo ""

# ---------- 对比配置 ----------
echo "--- 测试配置 ---"
printf "%-25s" "配置项"
for d in "${COMPARE_DIRS[@]}"; do
    label=$(basename "$d" | sed 's/_[0-9]*$//')
    printf "%-20s" "$label"
done
echo ""
printf "%-25s" "─────────────────────────"
for d in "${COMPARE_DIRS[@]}"; do
    printf "%-20s" "────────────────────"
done
echo ""

# 读取配置
declare -A CONFIG_KEYS
CONFIG_KEYS[git]="git"
CONFIG_KEYS[model]="model"
CONFIG_KEYS[n_threads]="n_threads"
CONFIG_KEYS[n_predict]="n_predict"
CONFIG_KEYS[seed]="seed"

for key in git model n_threads n_predict seed; do
    printf "%-25s" "$key"
    for d in "${COMPARE_DIRS[@]}"; do
        cfg="$d/config.csv"
        if [ -f "$cfg" ]; then
            val=$(tail -1 "$cfg" | cut -d',' -f$(head -1 "$cfg" | tr ',' '\n' | grep -n "^${key}$" | cut -d: -f1))
            printf "%-20s" "${val:---}"
        else
            printf "%-20s" "---"
        fi
    done
    echo ""
done

echo ""

# ---------- 对比时序 ----------
echo "--- 推理性能 ---"
printf "%-25s" "指标"
for d in "${COMPARE_DIRS[@]}"; do
    label=$(basename "$d" | sed 's/_[0-9]*$//')
    printf "%-20s" "$label"
done
echo ""
printf "%-25s" "─────────────────────────"
for d in "${COMPARE_DIRS[@]}"; do
    printf "%-20s" "────────────────────"
done
echo ""

for metric in load_time predict_time per_token_ms tokens_per_second; do
    case $metric in
        load_time)      DISPLAY="加载时间 (ms)" ;;
        predict_time)   DISPLAY="推理时间 (ms)" ;;
        per_token_ms)   DISPLAY="单 token 延迟 (ms)" ;;
        tokens_per_second) DISPLAY="吞吐量 (tok/s)" ;;
        *)              DISPLAY="$metric" ;;
    esac

    printf "%-25s" "$DISPLAY"
    for d in "${COMPARE_DIRS[@]}"; do
        timing="$d/timing.csv"
        if [ -f "$timing" ]; then
            val=$(grep "^${metric}," "$timing" | cut -d',' -f2)
            printf "%-20s" "${val:---}"
        else
            printf "%-20s" "---"
        fi
    done
    echo ""
done

echo ""

# ---------- 对比 STREAM 带宽 (如有) ----------
HAS_STREAM=0
for d in "${COMPARE_DIRS[@]}"; do
    if [ -f "$d/stream_results.csv" ]; then
        HAS_STREAM=1
        break
    fi
done

if [ $HAS_STREAM -eq 1 ]; then
    echo "--- DDR 带宽 (STREAM Triad) ---"
    printf "%-25s" "测试"
    for d in "${COMPARE_DIRS[@]}"; do
        label=$(basename "$d" | sed 's/_[0-9]*$//')
        printf "%-20s" "$label"
    done
    echo ""

    for test in "stream,1" "stream,4"; do
        NT=$(echo "$test" | cut -d',' -f2)
        printf "%-25s" "Triad ${NT}T (MB/s)"
        for d in "${COMPARE_DIRS[@]}"; do
            sf="$d/stream_results.csv"
            if [ -f "$sf" ]; then
                val=$(grep "$test" "$sf" | cut -d',' -f6)
                printf "%-20s" "${val:---}"
            else
                printf "%-20s" "---"
            fi
        done
        echo ""
    done
    echo ""
fi

# ---------- 对比 perf 数据 (如有) ----------
HAS_PERF=0
for d in "${COMPARE_DIRS[@]}"; do
    if [ -f "$d/perf_stat.log" ]; then
        HAS_PERF=1
        break
    fi
done

if [ $HAS_PERF -eq 1 ]; then
    echo "--- Perf 指标 ---"
    printf "%-25s" "指标"
    for d in "${COMPARE_DIRS[@]}"; do
        label=$(basename "$d" | sed 's/_[0-9]*$//')
        printf "%-20s" "$label"
    done
    echo ""
    printf "%-25s" "─────────────────────────"
    for d in "${COMPARE_DIRS[@]}"; do
        printf "%-20s" "────────────────────"
    done
    echo ""

    for metric_label in "cache-misses" "LLC-load-misses" "L1-dcache-load-misses"; do
        printf "%-25s" "$metric_label"
        for d in "${COMPARE_DIRS[@]}"; do
            pf="$d/perf_stat.log"
            if [ -f "$pf" ]; then
                val=$(grep "$metric_label" "$pf" | head -1 | awk '{print $1}')
                printf "%-20s" "${val:---}"
            else
                printf "%-20s" "---"
            fi
        done
        echo ""
    done
    echo ""
fi

# ---------- 加速比计算 ----------
if [ ${#COMPARE_DIRS[@]} -ge 2 ]; then
    FIRST="${COMPARE_DIRS[0]}"
    LAST="${COMPARE_DIRS[-1]}"
    
    t1_file="$FIRST/timing.csv"
    t2_file="$LAST/timing.csv"
    
    if [ -f "$t1_file" ] && [ -f "$t2_file" ]; then
        tps1=$(grep "^tokens_per_second," "$t1_file" | cut -d',' -f2)
        tps2=$(grep "^tokens_per_second," "$t2_file" | cut -d',' -f2)
        
        if [ -n "$tps1" ] && [ -n "$tps2" ] && [ "$tps1" != "0" ]; then
            SPEEDUP=$(echo "scale=2; $tps2 / $tps1" | bc 2>/dev/null || echo "N/A")
            FIRST_LABEL=$(basename "$FIRST" | sed 's/_[0-9]*$//')
            LAST_LABEL=$(basename "$LAST" | sed 's/_[0-9]*$//')
            echo "--- 加速比 ---"
            echo "$LAST_LABEL vs $FIRST_LABEL: ${SPEEDUP}x"
            echo ""
        fi
    fi
fi

echo "============================================"
echo "详细数据目录:"
for d in "${COMPARE_DIRS[@]}"; do
    echo "  $d"
done
