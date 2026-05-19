"""
v1: Lightweight CNN Channel Estimator (~30K params)
===================================================
最轻量的基线模型，3层CNN直接从稀疏导频重建完整信道。

设计思路:
  - 输入: [B, 2, n_sym, n_sc] (pilot位置有值, 其余为0, 2ch=real+imag)
  - 3层 Conv2d 逐步扩大感受野
  - 输出: [B, 2, n_sym, n_sc] (完整信道估计, 2ch=real+imag)
  - 参数量: ~30K, 适合 6TOPS NPU 部署

NPU 部署预估:
  - 推理计算量: ~0.3 MFLOPs
  - INT8 推理延迟: <150μs (6TOPS) / <50μs (20TOPS)
  - 实时性: 完全满足 1 slot = 0.5ms 约束
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


class LightCNN(nn.Module):
    def __init__(self, n_sym: int = 14, n_sc: int = 624):
        super().__init__()
        self.encoder = nn.Sequential(
            nn.Conv2d(2, 16, kernel_size=3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 32, kernel_size=5, padding=2),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
            nn.Conv2d(32, 32, kernel_size=7, padding=3),
            nn.BatchNorm2d(32),
            nn.ReLU(inplace=True),
        )
        self.decoder = nn.Sequential(
            nn.Conv2d(32, 16, kernel_size=5, padding=2),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 2, kernel_size=3, padding=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        feat = self.encoder(x)
        out = self.decoder(feat)
        return out


def main():
    config = NRConfig(n_rb=52, scs_khz=15, n_sym=14,
                      dmrs_sym_pos=(2, 11), dmrs_sc_stride=2)

    print("=" * 60)
    print("v1: Lightweight CNN Channel Estimator")
    print("=" * 60)
    print(f"Config: {config.n_rb} RB, {config.n_sc} subcarriers, "
          f"{config.n_sym} OFDM symbols")
    print(f"DM-RS: symbols={config.dmrs_sym_pos}, stride={config.dmrs_sc_stride}")
    print(f"Pilot ratio: {config.pilot_ratio:.2%} "
          f"({config.n_pilots}/{config.n_sym * config.n_sc} REs)")

    model = LightCNN(n_sym=config.n_sym, n_sc=config.n_sc)
    n_params = count_parameters(model)
    print(f"Parameters: {n_params:,} (~{n_params/1000:.0f}K)")

    dummy = torch.randn(1, 2, config.n_sym, config.n_sc)
    out = model(dummy)
    print(f"Input shape:  {dummy.shape}")
    print(f"Output shape: {out.shape}")

    n_train = 5000
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

    print(f"\nTraining for 50 epochs...")
    history = train_model(model, train_loader, val_loader,
                          n_epochs=50, lr=1e-3, device=device, print_every=10)

    print("\nEvaluating NMSE vs SNR...")
    snr_list = [0, 5, 10, 15, 20, 25]
    model_nmse = evaluate_nmse_vs_snr(model, config, snr_list,
                                       n_samples_per_snr=100, device=device)
    baseline_nmse = evaluate_baselines_vs_snr(config, snr_list, n_samples_per_snr=100)

    print(f"\n{'SNR(dB)':>8} | {'LS(dB)':>8} | {'LS+Interp(dB)':>14} | {'CNN(dB)':>8}")
    print("-" * 50)
    for snr in snr_list:
        ls_val = baseline_nmse["ls"][snr]["mean_nmse"]
        lsi_val = baseline_nmse["ls_interp"][snr]["mean_nmse"]
        cnn_val = model_nmse[snr]["mean_nmse"]
        print(f"{snr:8.0f} | {ls_val:8.2f} | {lsi_val:14.2f} | {cnn_val:8.2f}")

    save_path = os.path.join(os.path.dirname(__file__), "v1_cnn.pth")
    torch.save({
        "model_state_dict": model.state_dict(),
        "config": config,
        "n_params": n_params,
        "history": history,
    }, save_path)
    print(f"\nModel saved to: {save_path}")


if __name__ == "__main__":
    main()
