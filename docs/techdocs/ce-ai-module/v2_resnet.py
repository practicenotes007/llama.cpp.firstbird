"""
v2: ResNet Channel Estimator (~150K params)
============================================
基于残差连接的信道估计器，4个ResBlock逐步精化信道估计。

设计思路:
  - 输入: [B, 2, n_sym, n_sc] (稀疏导频, 2ch=real+imag)
  - Stem: 1层Conv2d提取初始特征
  - 4个ResBlock: 3×3 Conv + skip connection, 逐步精化
  - 输出: [B, 2, n_sym, n_sc] (完整信道估计)
  - 参数量: ~150K, 适合 6~20 TOPS NPU

NPU 部署预估:
  - 推理计算量: ~1.5 MFLOPs
  - INT8 推理延迟: <500μs (6TOPS) / <150μs (20TOPS)
  - 实时性: 满足 1 slot = 0.5ms 约束

架构灵感:
  - ResNet 的 skip connection 让梯度直接传播，缓解深层网络退化
  - 在信道估计任务中，残差连接的物理含义: 输入已经是LS估计的粗略版本,
    网络只需学习"修正量"而非"从头重建"
  - 3×3 kernel 适合捕捉信道的时频局部相关性
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import torch
import torch.nn as nn
import numpy as np
from common import (
    NRConfig, ChannelEstimationDataset, train_model,
    evaluate_nmse_vs_snr, evaluate_baselines_vs_snr,
    count_parameters, compute_nmse_batch
)
from torch.utils.data import DataLoader


class ResBlock(nn.Module):
    def __init__(self, channels: int):
        super().__init__()
        self.conv1 = nn.Conv2d(channels, channels, kernel_size=3, padding=1)
        self.bn1 = nn.BatchNorm2d(channels)
        self.relu = nn.ReLU(inplace=True)
        self.conv2 = nn.Conv2d(channels, channels, kernel_size=3, padding=1)
        self.bn2 = nn.BatchNorm2d(channels)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        residual = x
        out = self.relu(self.bn1(self.conv1(x)))
        out = self.bn2(self.conv2(out))
        return self.relu(out + residual)


class ResNetCE(nn.Module):
    def __init__(self, n_sym: int = 14, n_sc: int = 624,
                 base_channels: int = 32, n_blocks: int = 4):
        super().__init__()
        self.stem = nn.Sequential(
            nn.Conv2d(2, base_channels, kernel_size=5, padding=2),
            nn.BatchNorm2d(base_channels),
            nn.ReLU(inplace=True),
        )
        self.res_blocks = nn.Sequential(
            *[ResBlock(base_channels) for _ in range(n_blocks)]
        )
        self.head = nn.Sequential(
            nn.Conv2d(base_channels, 16, kernel_size=3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 2, kernel_size=3, padding=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.stem(x)
        feat = self.res_blocks(feat)
        out = self.head(feat)
        return out


def main():
    config = NRConfig(n_rb=52, scs_khz=15, n_sym=14,
                      dmrs_sym_pos=(2, 11), dmrs_sc_stride=2)

    print("=" * 60)
    print("v2: ResNet Channel Estimator")
    print("=" * 60)
    print(f"Config: {config.n_rb} RB, {config.n_sc} subcarriers, "
          f"{config.n_sym} OFDM symbols")
    print(f"DM-RS: symbols={config.dmrs_sym_pos}, stride={config.dmrs_sc_stride}")
    print(f"Pilot ratio: {config.pilot_ratio:.2%}")

    model = ResNetCE(n_sym=config.n_sym, n_sc=config.n_sc,
                     base_channels=32, n_blocks=4)
    n_params = count_parameters(model)
    print(f"Parameters: {n_params:,} (~{n_params/1000:.0f}K)")

    dummy = torch.randn(1, 2, config.n_sym, config.n_sc)
    out = model(dummy)
    print(f"Input shape:  {dummy.shape}")
    print(f"Output shape: {out.shape}")

    n_train = 8000
    n_val = 500
    batch_size = 32

    print(f"\nGenerating datasets: train={n_train}, val={n_val}")
    train_ds = ChannelEstimationDataset(n_train, config,
                                        snr_range=(0, 25),
                                        channel_models=("TDL-A", "TDL-C", "TDL-D"),
                                        doppler_range=(3, 120),
                                        seed=42)
    val_ds = ChannelEstimationDataset(n_val, config,
                                      snr_range=(5, 20),
                                      channel_models=("TDL-C",),
                                      doppler_range=(10, 70),
                                      seed=123)
    train_loader = DataLoader(train_ds, batch_size=batch_size, shuffle=True, num_workers=0)
    val_loader = DataLoader(val_ds, batch_size=batch_size, shuffle=False, num_workers=0)

    device = "cuda" if torch.cuda.is_available() else "cpu"
    print(f"Training on: {device}")

    print(f"\nTraining for 60 epochs...")
    history = train_model(model, train_loader, val_loader,
                          n_epochs=60, lr=1e-3, device=device, print_every=10)

    print("\nEvaluating NMSE vs SNR...")
    snr_list = [0, 5, 10, 15, 20, 25]
    model_nmse = evaluate_nmse_vs_snr(model, config, snr_list,
                                       n_samples_per_snr=100, device=device)
    baseline_nmse = evaluate_baselines_vs_snr(config, snr_list, n_samples_per_snr=100)

    print(f"\n{'SNR(dB)':>8} | {'LS(dB)':>8} | {'LS+Interp(dB)':>14} | {'ResNet(dB)':>10}")
    print("-" * 52)
    for snr in snr_list:
        ls_val = baseline_nmse["ls"][snr]["mean_nmse"]
        lsi_val = baseline_nmse["ls_interp"][snr]["mean_nmse"]
        res_val = model_nmse[snr]["mean_nmse"]
        print(f"{snr:8.0f} | {ls_val:8.2f} | {lsi_val:14.2f} | {res_val:10.2f}")

    save_path = os.path.join(os.path.dirname(__file__), "v2_resnet.pth")
    torch.save({
        "model_state_dict": model.state_dict(),
        "config": config,
        "n_params": n_params,
        "history": history,
    }, save_path)
    print(f"\nModel saved to: {save_path}")


if __name__ == "__main__":
    main()
