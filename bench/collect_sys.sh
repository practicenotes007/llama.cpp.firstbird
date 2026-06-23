#!/bin/bash
# collect_sys.sh - 后台系统指标采集器
# 用法: collect_sys.sh <输出目录> <采集间隔秒>
# 采集期间在后台运行，向输出目录写入多个指标文件
# 停止: kill $(cat <输出目录>/collect_sys.pid)

set -euo pipefail

OUTDIR="${1:?用法: collect_sys.sh <输出目录> [采集间隔秒]}"
INTERVAL="${2:-2}"

mkdir -p "$OUTDIR"

PIDFILE="$OUTDIR/collect_sys.pid"
if [ -f "$PIDFILE" ]; then
    OLD_PID=$(cat "$PIDFILE")
    if kill -0 "$OLD_PID" 2>/dev/null; then
        echo "已有采集进程运行 (PID $OLD_PID)，先停止"
        kill "$OLD_PID" 2>/dev/null || true
        sleep 1
    fi
fi

echo $$ > "$PIDFILE"

# 采集开始时间戳
DATE_TAG=$(date +%Y%m%d_%H%M%S)

# ---------- 静态系统信息（采集一次） ----------
SYSINFO="$OUTDIR/sysinfo_${DATE_TAG}.txt"
{
    echo "=== collect_sys 系统快照 ==="
    echo "采集时间: $(date)"
    echo "主机名: $(hostname)"
    echo "内核: $(uname -a)"
    echo ""
    echo "--- CPU 信息 ---"
    grep -E "model name|processor|cpu cores|Hardware" /proc/cpuinfo 2>/dev/null | sort -u || true
    echo ""
    echo "--- CPU 容量 (big.LITTLE 识别) ---"
    for d in /sys/devices/system/cpu/cpu*/; do
        cap=$(cat "$d/cpu_capacity" 2>/dev/null || echo "N/A")
        freq=$(cat "${d}cpufreq/scaling_max_freq" 2>/dev/null || echo "N/A")
        echo "$(basename $d): capacity=$cap max_freq=$freq"
    done
    echo ""
    echo "--- 内存 ---"
    free -h
    echo ""
    echo "--- DDR 带宽相关 ---"
    cat /sys/class/devfreq/*/cur_freq 2>/dev/null || echo "无 devfreq 信息"
    cat /sys/class/devfreq/*/max_freq 2>/dev/null || echo "无 devfreq 信息"
    echo ""
    echo "--- CPU 当前频率 ---"
    for d in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
        echo "$(echo $d | cut -d/ -f8): $(cat $d 2>/dev/null || echo N/A) kHz"
    done
    echo ""
    echo "--- 温度 ---"
    for t in /sys/class/thermal/thermal_zone*/temp; do
        zone=$(echo $t | cut -d/ -f6)
        val=$(cat "$t" 2>/dev/null || echo "N/A")
        if [ "$val" != "N/A" ]; then
            echo "$zone: $((val/1000)) C"
        else
            echo "$zone: N/A"
        fi
    done
    echo ""
    echo "--- 磁盘 ---"
    df -h / 2>/dev/null || true
    echo ""
    echo "--- perf 可用性 ---"
    perf --version 2>/dev/null && echo "perf 可用" || echo "perf 不可用"
} > "$SYSINFO"

# ---------- 动态指标文件 ----------
PERF_STAT_FILE="$OUTDIR/perf_stat_${DATE_TAG}.log"
TOP_FILE="$OUTDIR/top_snapshot_${DATE_TAG}.log"
VMSTAT_FILE="$OUTDIR/vmstat_${DATE_TAG}.log"
CPUFREQ_FILE="$OUTDIR/cpufreq_${DATE_TAG}.log"
THERMAL_FILE="$OUTDIR/thermal_${DATE_TAG}.log"

# 写入 CSV 表头
echo "timestamp,cpu0,cpu1,cpu2,cpu3,cpu4,cpu5,cpu6,cpu7" > "$CPUFREQ_FILE"
echo "timestamp,zone0,zone1,zone2,zone3,zone4,zone5,zone6,zone7" > "$THERMAL_FILE"

cleanup() {
    echo "collect_sys: 停止采集" >&2
    rm -f "$PIDFILE"
    # 停止可能存在的 perf 子进程
    jobs -p | xargs kill 2>/dev/null || true
    exit 0
}
trap cleanup EXIT INT TERM

echo "collect_sys: 开始采集 (PID $$, 间隔 ${INTERVAL}s, 输出 $OUTDIR)"

CYCLE=0
while true; do
    TS=$(date +%s%3N)

    # top 快照 (一次完整输出)
    top -b -n1 -w 120 > "${TOP_FILE}.tmp" 2>/dev/null || true
    mv "${TOP_FILE}.tmp" "$TOP_FILE"

    # vmstat
    vmstat 1 2 > "${VMSTAT_FILE}.tmp" 2>/dev/null || true
    echo "=== cycle=$CYCLE ts=$TS ===" >> "$VMSTAT_FILE"
    cat "${VMSTAT_FILE}.tmp" >> "$VMSTAT_FILE"
    rm -f "${VMSTAT_FILE}.tmp"

    # CPU 频率 (CSV 追加)
    FREQ_LINE="$TS"
    for d in /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq; do
        f=$(cat "$d" 2>/dev/null || echo "0")
        FREQ_LINE="$FREQ_LINE,$f"
    done
    echo "$FREQ_LINE" >> "$CPUFREQ_FILE"

    # 温度 (CSV 追加)
    THERM_LINE="$TS"
    for t in /sys/class/thermal/thermal_zone*/temp; do
        v=$(cat "$t" 2>/dev/null || echo "0")
        THERM_LINE="$THERM_LINE,$((v/1000))"
    done
    echo "$THERM_LINE" >> "$THERMAL_FILE"

    CYCLE=$((CYCLE + 1))
    sleep "$INTERVAL"
done
