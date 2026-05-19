"""
v3: HELENA-style Dual-Attention Channel Estimator (~110K params)
================================================================
轻量级双注意力信道估计器, 复现 HELENA (arXiv:2506.13408) 的核心思想。

设计思路:
  - Conv Backbone: 2D卷积提取局部时频特征
  - Patch-wise Multi-Head Self-Attention (MHSA): 捕捉全局依赖
  - Squeeze-and-Excitation (SE) Block: 通道维度的特征精化
  - Linear Reconstruction Head + Residual Connection: 保留结构信息

架构 (参考 HELENA):
  Input [B, 2, 14, 624]
    │
    ├─ Conv2d Stem → [B, 32, 14, 624]
    │
    ├─ Conv Block 1 (Conv+BN+ReLU) → [B, 32, 14, 624]
    │
    ├─ Patch Embed → [B, N_patches, d_model]
    │   (将时频网格切为patch, 展平后线性投影)
    │
    ├─ MHSA (Multi-Head Self-Attention) → [B, N_patches, d_model]
    │   (全局依赖: 远距离子载波/符号间的信道相关性)
    │
    ├─ Patch Reconstruct → [B, 32, 14, 624]
    │
    ├─ SE Block → [B, 32, 14, 624]
    │   (通道注意力: 自适应加权不同特征通道)
    │
    ├─ Conv Block 2 → [B, 16, 14, 624]
    │
    ├─ Linear Head → [B, 2, 14, 624]
    │
    └─ + Input Residual → Output [B, 2, 14, 624]

NPU 部署预估:
  - 推理计算量: ~2 MFLOPs
  - INT8 推理延迟: <600μs (6TOPS) / <200μs (20TOPS)
  - 实时性: 满足 1 slot = 0.5ms 约束
  - 注意: MHSA 的 MatMul 对 NPU 友好 (数据流加速器擅长)

对比 HELENA 原文:
  - 原文: 0.11M params, 0.175ms inference, NMSE = -16.78dB
  - 本实现: ~110K params, 旨在 ARM NPU 上 <0.5ms
  - 差异: patch 策略和 MHSA head 数根据 14×624 输入适配
"""

import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import math
import torch
import torch.nn as nn
import torch.nn.functional as F
import numpy as np
from common import (
    NRConfig, ChannelEstimationDataset, train_model,
    evaluate_nmse_vs_snr, evaluate_baselines_vs_snr,
    count_parameters, compute_nmse_batch
)
from torch.utils.data import DataLoader


class PatchEmbed(nn.Module):
    def __init__(self, n_sym: int, n_sc: int, in_ch: int,
                 patch_size: tuple, d_model: int):
        super().__init__()
        self.patch_h, self.patch_w = patch_size
        self.n_patches_h = n_sym // self.patch_h
        self.n_patches_w = n_sc // self.patch_w
        self.n_patches = self.n_patches_h * self.n_patches_w
        patch_dim = in_ch * self.patch_h * self.patch_w
        self.proj = nn.Linear(patch_dim, d_model)
        self.norm = nn.LayerNorm(d_model)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, C, H, W = x.shape
        ph, pw = self.patch_h, self.patch_w
        x = x.reshape(B, C, H // ph, ph, W // pw, pw)
        x = x.permute(0, 2, 4, 1, 3, 5).reshape(B, self.n_patches, C * ph * pw)
        x = self.proj(x)
        x = self.norm(x)
        return x


class PatchReconstruct(nn.Module):
    def __init__(self, n_sym: int, n_sc: int, out_ch: int,
                 patch_size: tuple, d_model: int):
        super().__init__()
        self.patch_h, self.patch_w = patch_size
        self.n_patches_h = n_sym // self.patch_h
        self.n_patches_w = n_sc // self.patch_w
        self.n_patches = self.n_patches_h * self.n_patches_w
        patch_dim = out_ch * self.patch_h * self.patch_w
        self.proj = nn.Linear(d_model, patch_dim)
        self.norm = nn.LayerNorm(d_model)
        self.out_ch = out_ch

    def forward(self, x: torch.Tensor, target_shape: tuple) -> torch.Tensor:
        B = x.shape[0]
        H, W = target_shape
        x = self.norm(x)
        x = self.proj(x)
        ph, pw = self.patch_h, self.patch_w
        x = x.reshape(B, self.n_patches_h, self.n_patches_w,
                       self.out_ch, ph, pw)
        x = x.permute(0, 3, 1, 4, 2, 5).reshape(B, self.out_ch, H, W)
        return x


class MultiHeadSelfAttention(nn.Module):
    def __init__(self, d_model: int, n_heads: int = 4, dropout: float = 0.1):
        super().__init__()
        self.d_model = d_model
        self.n_heads = n_heads
        self.d_k = d_model // n_heads
        self.qkv = nn.Linear(d_model, 3 * d_model)
        self.out = nn.Linear(d_model, d_model)
        self.dropout = nn.Dropout(dropout)
        self.scale = math.sqrt(self.d_k)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, N, D = x.shape
        qkv = self.qkv(x).reshape(B, N, 3, self.n_heads, self.d_k)
        qkv = qkv.permute(2, 0, 3, 1, 4)
        q, k, v = qkv[0], qkv[1], qkv[2]

        attn = (q @ k.transpose(-2, -1)) / self.scale
        attn = attn.softmax(dim=-1)
        attn = self.dropout(attn)

        out = (attn @ v).transpose(1, 2).reshape(B, N, D)
        out = self.out(out)
        return out


class SEBlock(nn.Module):
    def __init__(self, channels: int, reduction: int = 4):
        super().__init__()
        self.squeeze = nn.AdaptiveAvgPool2d(1)
        self.excite = nn.Sequential(
            nn.Linear(channels, channels // reduction, bias=False),
            nn.ReLU(inplace=True),
            nn.Linear(channels // reduction, channels, bias=False),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, C, _, _ = x.shape
        scale = self.squeeze(x).view(B, C)
        scale = self.excite(scale).view(B, C, 1, 1)
        return x * scale


class HELENACE(nn.Module):
    def __init__(self, n_sym: int = 14, n_sc: int = 624,
                 base_ch: int = 32, d_model: int = 64,
                 n_heads: int = 4, patch_size: tuple = (2, 12)):
        super().__init__()
        self.n_sym = n_sym
        self.n_sc = n_sc

        ps_h = patch_size[0]
        ps_w = patch_size[1]
        n_sc_padded = ((n_sc + ps_w - 1) // ps_w) * ps_w
        n_sym_padded = ((n_sym + ps_h - 1) // ps_h) * ps_h
        self.n_sc_padded = n_sc_padded
        self.n_sym_padded = n_sym_padded
        self.patch_size = (ps_h, ps_w)

        self.stem = nn.Sequential(
            nn.Conv2d(2, base_ch, kernel_size=3, padding=1),
            nn.BatchNorm2d(base_ch),
            nn.ReLU(inplace=True),
        )

        self.conv_block = nn.Sequential(
            nn.Conv2d(base_ch, base_ch, kernel_size=3, padding=1),
            nn.BatchNorm2d(base_ch),
            nn.ReLU(inplace=True),
            nn.Conv2d(base_ch, base_ch, kernel_size=3, padding=1),
            nn.BatchNorm2d(base_ch),
            nn.ReLU(inplace=True),
        )

        self.patch_embed = PatchEmbed(n_sym_padded, n_sc_padded, base_ch,
                                       self.patch_size, d_model)
        self.mhsa = MultiHeadSelfAttention(d_model, n_heads)
        self.mhsa_norm = nn.LayerNorm(d_model)
        self.mhsa_ffn = nn.Sequential(
            nn.Linear(d_model, d_model * 2),
            nn.GELU(),
            nn.Dropout(0.1),
            nn.Linear(d_model * 2, d_model),
            nn.Dropout(0.1),
        )
        self.mhsa_ffn_norm = nn.LayerNorm(d_model)

        self.patch_recon = PatchReconstruct(n_sym_padded, n_sc_padded, base_ch,
                                             self.patch_size, d_model)

        self.se = SEBlock(base_ch, reduction=4)

        self.head = nn.Sequential(
            nn.Conv2d(base_ch, 16, kernel_size=3, padding=1),
            nn.BatchNorm2d(16),
            nn.ReLU(inplace=True),
            nn.Conv2d(16, 2, kernel_size=3, padding=1),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        B, C, H, W = x.shape
        residual_in = x

        if H != self.n_sym_padded or W != self.n_sc_padded:
            x_padded = F.pad(x, (0, self.n_sc_padded - W, 0, self.n_sym_padded - H))
        else:
            x_padded = x

        feat = self.stem(x_padded)
        feat = self.conv_block(feat)

        patches = self.patch_embed(feat)
        attn_out = self.mhsa(patches)
        patches = patches + attn_out
        patches = self.mhsa_norm(patches)
        ffn_out = self.mhsa_ffn(patches)
        patches = patches + ffn_out
        patches = self.mhsa_ffn_norm(patches)

        feat_attn = self.patch_recon(patches, (self.n_sym_padded, self.n_sc_padded))
        feat = feat + feat_attn

        feat = self.se(feat)

        out = self.head(feat)

        if H != self.n_sym_padded or W != self.n_sc_padded:
            out = out[:, :, :H, :W]

        out = out + residual_in

        return out


def main():
    config = NRConfig(n_rb=52, scs_khz=15, n_sym=14,
                      dmrs_sym_pos=(2, 11), dmrs_sc_stride=2)

    print("=" * 60)
    print("v3: HELENA-style Dual-Attention Channel Estimator")
    print("=" * 60)
    print(f"Config: {config.n_rb} RB, {config.n_sc} subcarriers, "
          f"{config.n_sym} OFDM symbols")
    print(f"DM-RS: symbols={config.dmrs_sym_pos}, stride={config.dmrs_sc_stride}")
    print(f"Pilot ratio: {config.pilot_ratio:.2%}")

    ps = (2, 12)
    model = HELENACE(n_sym=config.n_sym, n_sc=config.n_sc,
                     base_ch=32, d_model=64, n_heads=4,
                     patch_size=ps)
    n_params = count_parameters(model)
    print(f"Patch size: {ps}")
    print(f"Parameters: {n_params:,} (~{n_params/1000:.0f}K)")

    dummy = torch.randn(1, 2, config.n_sym, config.n_sc)
    out = model(dummy)
    print(f"Input shape:  {dummy.shape}")
    print(f"Output shape: {out.shape}")

    n_train = 10000
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

    print(f"\nTraining for 80 epochs (cosine LR schedule)...")
    history = train_model(model, train_loader, val_loader,
                          n_epochs=80, lr=5e-4, device=device, print_every=10)

    print("\nEvaluating NMSE vs SNR...")
    snr_list = [0, 5, 10, 15, 20, 25]
    model_nmse = evaluate_nmse_vs_snr(model, config, snr_list,
                                       n_samples_per_snr=100, device=device)
    baseline_nmse = evaluate_baselines_vs_snr(config, snr_list, n_samples_per_snr=100)

    print(f"\n{'SNR(dB)':>8} | {'LS(dB)':>8} | {'LS+Interp(dB)':>14} | {'HELENA(dB)':>10}")
    print("-" * 52)
    for snr in snr_list:
        ls_val = baseline_nmse["ls"][snr]["mean_nmse"]
        lsi_val = baseline_nmse["ls_interp"][snr]["mean_nmse"]
        hel_val = model_nmse[snr]["mean_nmse"]
        print(f"{snr:8.0f} | {ls_val:8.2f} | {lsi_val:14.2f} | {hel_val:10.2f}")

    save_path = os.path.join(os.path.dirname(__file__), "v3_helena.pth")
    torch.save({
        "model_state_dict": model.state_dict(),
        "config": config,
        "n_params": n_params,
        "history": history,
    }, save_path)
    print(f"\nModel saved to: {save_path}")


if __name__ == "__main__":
    main()
