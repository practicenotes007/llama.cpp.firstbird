#!/usr/bin/env python3
"""
plot_results.py - 基准测试结果可视化

用法:
  # 绘制单次测试的系统指标
  python3 plot_results.py bench/results/<标签>_<时间>/

  # 对比多次测试的 token/s
  python3 plot_results.py bench/results/<标签1> ... bench/results/<标签N>

  # 对比最近 N 次
  python3 plot_results.py --last 3

输出: 在结果目录内生成 PNG 图片
"""

import os
import sys
import glob
import argparse
from pathlib import Path

try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    import numpy as np
    HAVE_MPL = True
except ImportError:
    HAVE_MPL = False
    print("警告: 未安装 matplotlib，无法绘图。")
    print("  pip install matplotlib numpy")


def parse_config(csv_path):
    """解析 config.csv，返回 dict"""
    cfg = {}
    if not os.path.isfile(csv_path):
        return cfg
    with open(csv_path) as f:
        lines = f.readlines()
    if len(lines) < 2:
        return cfg
    headers = [h.strip() for h in lines[0].split(',')]
    values = [v.strip() for v in lines[1].split(',')]
    for h, v in zip(headers, values):
        cfg[h] = v
    return cfg


def parse_timing(csv_path):
    """解析 timing.csv，返回 dict{metric: value}"""
    data = {}
    if not os.path.isfile(csv_path):
        return data
    with open(csv_path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('metric'):
                continue
            parts = line.split(',')
            if len(parts) >= 2:
                data[parts[0]] = parts[1]
    return data


def parse_cpufreq(csv_path):
    """解析 cpufreq_*.csv，返回 (timestamps, cores)"""
    if not os.path.isfile(csv_path):
        return None, None
    ts = []
    cores = []
    with open(csv_path) as f:
        for i, line in enumerate(f):
            if i == 0:  # header
                n_cores = len(line.strip().split(',')) - 1
                continue
            parts = line.strip().split(',')
            if len(parts) < 2:
                continue
            ts.append(int(parts[0]) / 1000.0)  # ms -> s
            if cores:
                for j in range(1, len(parts)):
                    if j - 1 < len(cores):
                        cores[j - 1].append(float(parts[j]))
            else:
                cores = [[float(parts[j]) for j in range(1, len(parts))]]
    # Normalize timestamps to start at 0
    if ts:
        t0 = ts[0]
        ts = [t - t0 for t in ts]
    return ts, cores


def parse_thermal(csv_path):
    """解析 thermal_*.csv，返回 (timestamps, zones)"""
    if not os.path.isfile(csv_path):
        return None, None
    ts = []
    zones = []
    with open(csv_path) as f:
        for i, line in enumerate(f):
            if i == 0:
                continue
            parts = line.strip().split(',')
            if len(parts) < 2:
                continue
            ts.append(int(parts[0]) / 1000.0)
            if zones:
                for j in range(1, len(parts)):
                    if j - 1 < len(zones):
                        zones[j - 1].append(float(parts[j]))
            else:
                zones = [[float(parts[j]) for j in range(1, len(parts))]]
    if ts:
        t0 = ts[0]
        ts = [t - t0 for t in ts]
    return ts, zones


def plot_single_run(result_dir):
    """绘制单次测试的系统指标"""
    label = os.path.basename(result_dir)
    cpufreq_files = glob.glob(os.path.join(result_dir, "cpufreq_*.log"))
    thermal_files = glob.glob(os.path.join(result_dir, "thermal_*.log"))
    timing = parse_timing(os.path.join(result_dir, "timing.csv"))

    n_plots = 0
    if cpufreq_files:
        n_plots += 1
    if thermal_files:
        n_plots += 1

    if n_plots == 0:
        return

    fig, axes = plt.subplots(n_plots, 1, figsize=(12, 4 * n_plots), sharex=True)
    if n_plots == 1:
        axes = [axes]

    plot_idx = 0

    # CPU 频率
    if cpufreq_files:
        ax = axes[plot_idx]
        ts, cores = parse_cpufreq(cpufreq_files[0])
        if ts and cores:
            for ci, core_data in enumerate(cores):
                ax.plot(ts, [c / 1000 for c in core_data],
                        label=f'CPU{ci}', linewidth=0.8)
            ax.set_ylabel('Freq (MHz)')
            ax.set_title(f'{label} - CPU Frequency')
            ax.legend(fontsize=8, ncol=4)
            ax.grid(True, alpha=0.3)
            # 标注 tok/s
            if 'tokens_per_second' in timing:
                ax.text(0.98, 0.95, f"{timing['tokens_per_second']} tok/s",
                        transform=ax.transAxes, ha='right', va='top',
                        bbox=dict(boxstyle='round', facecolor='wheat', alpha=0.5))
        plot_idx += 1

    # 温度
    if thermal_files:
        ax = axes[plot_idx]
        ts, zones = parse_thermal(thermal_files[0])
        if ts and zones:
            for zi, zone_data in enumerate(zones):
                ax.plot(ts, zone_data, label=f'Zone{zi}', linewidth=0.8)
            ax.set_ylabel('Temp (C)')
            ax.set_xlabel('Time (s)')
            ax.set_title(f'{label} - Temperature')
            ax.legend(fontsize=8, ncol=4)
            ax.grid(True, alpha=0.3)
        plot_idx += 1

    plt.tight_layout()
    out_path = os.path.join(result_dir, 'sys_plots.png')
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  系统指标图: {out_path}")


def plot_comparison(result_dirs):
    """绘制多次测试的 token/s 对比"""
    labels = []
    tps_values = []
    per_token_values = []
    load_time_values = []

    for d in result_dirs:
        label = os.path.basename(d).rsplit('_', 1)[0]
        timing = parse_timing(os.path.join(d, "timing.csv"))
        labels.append(label)
        tps_values.append(float(timing.get('tokens_per_second', 0)))
        per_token_values.append(float(timing.get('per_token_ms', 0)))
        load_time_values.append(float(timing.get('load_time', 0)))

    if not tps_values:
        print("  没有找到时序数据")
        return

    fig, axes = plt.subplots(1, 3, figsize=(15, 5))
    x = np.arange(len(labels))

    # tok/s
    ax = axes[0]
    colors = ['#2196F3', '#4CAF50', '#FF9800', '#F44336', '#9C27B0', '#00BCD4']
    bars = ax.bar(x, tps_values, color=colors[:len(labels)])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('tok/s')
    ax.set_title('Throughput')
    ax.grid(True, alpha=0.3, axis='y')
    for bar, val in zip(bars, tps_values):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.1,
                f'{val:.1f}', ha='center', va='bottom', fontsize=9)

    # per-token ms
    ax = axes[1]
    ax.bar(x, per_token_values, color=colors[:len(labels)])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('ms/token')
    ax.set_title('Latency (lower is better)')
    ax.grid(True, alpha=0.3, axis='y')

    # load time
    ax = axes[2]
    ax.bar(x, [v / 1000 for v in load_time_values], color=colors[:len(labels)])
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('s')
    ax.set_title('Model Load Time')
    ax.grid(True, alpha=0.3, axis='y')

    plt.tight_layout()

    # Save to the latest result dir, or current dir
    out_dir = result_dirs[-1] if result_dirs else '.'
    out_path = os.path.join(out_dir, 'comparison.png')
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  对比图: {out_path}")


def plot_trend(result_dirs):
    """绘制 token/s 随测试版本变化的趋势"""
    labels = []
    tps_values = []

    for d in result_dirs:
        label = os.path.basename(d).rsplit('_', 1)[0]
        timing = parse_timing(os.path.join(d, "timing.csv"))
        labels.append(label)
        tps_values.append(float(timing.get('tokens_per_second', 0)))

    if len(tps_values) < 2:
        return

    fig, ax = plt.subplots(figsize=(10, 5))
    x = np.arange(len(labels))

    ax.plot(x, tps_values, '-o', linewidth=2, markersize=8, color='#2196F3')
    ax.fill_between(x, tps_values, alpha=0.15, color='#2196F3')
    ax.set_xticks(x)
    ax.set_xticklabels(labels, rotation=30, ha='right', fontsize=9)
    ax.set_ylabel('tok/s')
    ax.set_title('Performance Trend')
    ax.grid(True, alpha=0.3)

    for i, v in enumerate(tps_values):
        ax.text(i, v + 0.1, f'{v:.2f}', ha='center', fontsize=10)

    plt.tight_layout()
    out_dir = result_dirs[-1] if result_dirs else '.'
    out_path = os.path.join(out_dir, 'trend.png')
    plt.savefig(out_path, dpi=150)
    plt.close()
    print(f"  趋势图: {out_path}")


def main():
    parser = argparse.ArgumentParser(description='基准测试结果可视化')
    parser.add_argument('dirs', nargs='*', help='结果目录')
    parser.add_argument('--last', type=int, help='最近 N 次测试')
    parser.add_argument('--single', action='store_true', help='只绘制单次系统指标')
    args = parser.parse_args()

    if not HAVE_MPL:
        return 1

    result_base = os.path.join(os.path.dirname(__file__), 'results')

    result_dirs = []
    if args.last:
        all_dirs = sorted(glob.glob(os.path.join(result_base, '*/')), reverse=True)
        result_dirs = all_dirs[:args.last]
    elif args.dirs:
        for d in args.dirs:
            if os.path.isdir(d):
                result_dirs.append(d)
            else:
                full = os.path.join(result_base, d)
                if os.path.isdir(full):
                    result_dirs.append(full)
    else:
        # 默认分析最近一次测试
        all_dirs = sorted(glob.glob(os.path.join(result_base, '*/')), reverse=True)
        if all_dirs:
            result_dirs = [all_dirs[0]]

    if not result_dirs:
        print("没有找到结果目录")
        return 1

    print(f"分析 {len(result_dirs)} 个结果目录:")

    if len(result_dirs) == 1 or args.single:
        for d in result_dirs:
            print(f"  {d}")
            plot_single_run(d)
    else:
        for d in result_dirs:
            print(f"  {d}")
        plot_comparison(result_dirs)
        plot_trend(result_dirs)

        # Also plot individual system metrics
        for d in result_dirs:
            plot_single_run(d)

    return 0


if __name__ == '__main__':
    sys.exit(main())
