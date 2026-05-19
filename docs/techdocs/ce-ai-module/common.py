"""
5G NR AI Channel Estimation - Common Module
============================================
5G NR 信道模拟器、DM-RS 配置、数据集生成、评价指标、基线估计器。

训练三元组: (X=pilot, H=true_channel, Y=received_signal)
  Y = H * X + N   (AWGN)
  模型输入: Y_at_pilots (稀疏, 仅DM-RS位置)
  模型输出: H_hat (完整信道矩阵)
  Loss: MSE(H_hat, H)

参考标准: 3GPP TS 38.211 (DM-RS), 3GPP TR 38.901 (信道模型)
"""

import numpy as np
import torch
from torch.utils.data import Dataset
from dataclasses import dataclass
from typing import Tuple, Optional


@dataclass
class NRConfig:
    n_rb: int = 52
    scs_khz: int = 15
    n_sym: int = 14
    dmrs_sym_pos: tuple = (2, 11)
    dmrs_sc_stride: int = 2
    cp_type: str = "normal"

    @property
    def n_sc(self) -> int:
        return self.n_rb * 12

    @property
    def dmrs_mask(self) -> np.ndarray:
        mask = np.zeros((self.n_sym, self.n_sc), dtype=bool)
        for s in self.dmrs_sym_pos:
            mask[s, ::self.dmrs_sc_stride] = True
        return mask

    @property
    def n_pilots(self) -> int:
        return int(self.dmrs_mask.sum())

    @property
    def pilot_ratio(self) -> float:
        return self.n_pilots / (self.n_sym * self.n_sc)


def generate_dmrs_seq(n_sc: int, dmrs_sc_stride: int, seed: int = 42) -> np.ndarray:
    rng = np.random.RandomState(seed)
    seq = np.zeros(n_sc, dtype=np.complex64)
    n_dmrs_sc = n_sc // dmrs_sc_stride
    gold_seq = (1 - 2 * rng.randint(0, 2, n_dmrs_sc)) + 1j * (1 - 2 * rng.randint(0, 2, n_dmrs_sc))
    gold_seq /= np.sqrt(2)
    seq[::dmrs_sc_stride] = gold_seq
    return seq


def generate_channel_tdl(n_sym: int, n_sc: int, delay_spread: float = 300e-9,
                         doppler_hz: float = 50.0, model: str = "TDL-C",
                         rng: Optional[np.random.RandomState] = None) -> np.ndarray:
    if rng is None:
        rng = np.random.RandomState()

    scs = 15e3
    ofdm_bw = n_sc * scs

    if model == "TDL-A":
        taps_delay_ns = np.array([0, 30, 70, 110, 190, 410, 830, 1200, 2300, 3700])
        taps_power_db = np.array([0, -1.7, -3.9, -5.6, -7.4, -9.1, -12.1, -14.3, -19.5, -24.2])
    elif model == "TDL-B":
        taps_delay_ns = np.array([0, 10, 20, 30, 50, 80, 110, 190, 370, 730, 1500, 3000])
        taps_power_db = np.array([-4.3, -1.2, -2.1, -2.7, -1.8, -3.1, -2.2, -4.1, -5.8, -8.2, -12.1, -16.5])
    elif model == "TDL-C":
        taps_delay_ns = np.array([0, 65, 70, 190, 260, 335, 500, 655, 880, 1200, 1500, 1850])
        taps_power_db = np.array([-4.4, -1.2, -3.5, -4.2, -5.2, -6.1, -7.7, -9.2, -11.5, -13.9, -15.7, -18.1])
    elif model == "TDL-D":
        taps_delay_ns = np.array([0, 10, 20, 35, 55, 85, 130, 200, 330, 560, 880, 1380, 2200, 3500])
        taps_power_db = np.array([-5.1, -1.4, -2.1, -3.5, -3.2, -4.1, -6.3, -7.7, -9.5, -12.1, -15.3, -18.4, -22.0, -26.1])
    else:
        taps_delay_ns = np.array([0, 30, 150, 310, 370, 710, 1090, 1730, 2510])
        taps_power_db = np.array([0, -2.2, -4.1, -7.3, -5.2, -9.8, -13.2, -17.1, -21.5])

    n_taps = len(taps_delay_ns)
    taps_power = 10 ** (taps_power_db / 10)
    taps_power = taps_power / taps_power.sum()

    delays_samples = taps_delay_ns * 1e-9 * ofdm_bw

    freq_grid = np.fft.fftfreq(n_sc, d=1.0 / ofdm_bw)
    H = np.zeros((n_sym, n_sc), dtype=np.complex64)

    for sym_idx in range(n_sym):
        t = sym_idx / (14 * scs)
        tap_gains = np.zeros(n_taps, dtype=np.complex64)
        for t_idx in range(n_taps):
            phase_inc = 2 * np.pi * doppler_hz * t
            jakes_phase = rng.uniform(0, 2 * np.pi)
            rayleigh_gain = (rng.randn() + 1j * rng.randn()) / np.sqrt(2)
            tap_gains[t_idx] = rayleigh_gain * np.sqrt(taps_power[t_idx]) * np.exp(1j * (jakes_phase + phase_inc))

        H_sym = np.zeros(n_sc, dtype=np.complex64)
        for t_idx in range(n_taps):
            delay_phase = -2j * np.pi * delays_samples[t_idx] * np.arange(n_sc) / n_sc
            H_sym += tap_gains[t_idx] * np.exp(delay_phase)

        H[sym_idx] = H_sym

    return H


def generate_triplet(config: NRConfig, snr_db: float, channel_model: str = "TDL-C",
                     doppler_hz: float = 50.0, rng: Optional[np.random.RandomState] = None) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    if rng is None:
        rng = np.random.RandomState()

    H = generate_channel_tdl(config.n_sym, config.n_sc, model=channel_model,
                             doppler_hz=doppler_hz, rng=rng)

    dmrs = generate_dmrs_seq(config.n_sc, config.dmrs_sc_stride, seed=rng.randint(0, 2**31))
    X = np.zeros((config.n_sym, config.n_sc), dtype=np.complex64)
    for s in config.dmrs_sym_pos:
        X[s] = dmrs

    noise_var = 10 ** (-snr_db / 10)
    N = np.sqrt(noise_var / 2) * (rng.randn(config.n_sym, config.n_sc) +
                                    1j * rng.randn(config.n_sym, config.n_sc))
    N = N.astype(np.complex64)

    Y = H * X + N

    return X, H, Y


def extract_pilot_features(Y: np.ndarray, mask: np.ndarray) -> np.ndarray:
    pilot_vals = Y[mask]
    n_sym, n_sc = Y.shape
    full_grid = np.zeros((n_sym, n_sc, 2), dtype=np.float32)
    pilot_indices = np.argwhere(mask)
    for idx in pilot_indices:
        s, c = idx
        full_grid[s, c, 0] = pilot_vals[np.sum(mask[:s, :]) + np.sum(mask[s, :c])].real if False else 0
    full_grid = np.zeros((n_sym, n_sc, 2), dtype=np.float32)
    full_grid[:, :, 0] = Y.real
    full_grid[:, :, 1] = Y.imag
    full_grid[~mask] = 0.0
    return full_grid


def channel_to_2ch(H: np.ndarray) -> np.ndarray:
    out = np.zeros((*H.shape, 2), dtype=np.float32)
    out[..., 0] = H.real
    out[..., 1] = H.imag
    return out


def channel_from_2ch(H2ch: np.ndarray) -> np.ndarray:
    return H2ch[..., 0] + 1j * H2ch[..., 1]


class ChannelEstimationDataset(Dataset):
    def __init__(self, n_samples: int, config: NRConfig, snr_range: Tuple[float, float] = (0.0, 25.0),
                 channel_models: Tuple[str, ...] = ("TDL-A", "TDL-C", "TDL-D"),
                 doppler_range: Tuple[float, float] = (3.0, 120.0),
                 seed: int = 42):
        self.n_samples = n_samples
        self.config = config
        self.snr_range = snr_range
        self.channel_models = channel_models
        self.doppler_range = doppler_range
        self.rng = np.random.RandomState(seed)
        self.mask = config.dmrs_mask
        self._cache = {}

    def __len__(self) -> int:
        return self.n_samples

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
        snr_db = self.rng.uniform(*self.snr_range)
        ch_model = self.channel_models[self.rng.randint(0, len(self.channel_models))]
        doppler = self.rng.uniform(*self.doppler_range)

        X, H, Y = generate_triplet(self.config, snr_db, ch_model, doppler, self.rng)

        pilot_input = extract_pilot_features(Y, self.mask)
        H_target = channel_to_2ch(H)
        mask_arr = self.mask.astype(np.float32)

        return (torch.from_numpy(pilot_input),
                torch.from_numpy(H_target),
                torch.from_numpy(mask_arr),
                torch.tensor(snr_db, dtype=torch.float32))


def ls_estimate(Y: np.ndarray, X: np.ndarray, mask: np.ndarray) -> np.ndarray:
    H_ls = np.zeros_like(Y)
    H_ls[mask] = Y[mask] / (X[mask] + 1e-12)
    return H_ls


def ls_interpolate(Y: np.ndarray, X: np.ndarray, mask: np.ndarray) -> np.ndarray:
    H_ls = ls_estimate(Y, X, mask)
    n_sym, n_sc = Y.shape

    for s in range(n_sym):
        pilot_sc = np.where(mask[s])[0]
        if len(pilot_sc) == 0:
            continue
        all_sc = np.arange(n_sc)
        H_ls[s] = np.interp(all_sc, pilot_sc, H_ls[s, pilot_sc])

    for c in range(n_sc):
        pilot_sym = np.where(mask[:, c])[0]
        if len(pilot_sym) < 2:
            continue
        all_sym = np.arange(n_sym)
        H_ls[:, c] = np.interp(all_sym, pilot_sym, H_ls[pilot_sym, c])

    return H_ls


def compute_nmse(H_hat: np.ndarray, H_true: np.ndarray) -> float:
    num = np.mean(np.abs(H_hat - H_true) ** 2)
    den = np.mean(np.abs(H_true) ** 2)
    if den < 1e-12:
        return 0.0
    return 10 * np.log10(num / den + 1e-12)


def compute_nmse_batch(H_hat: torch.Tensor, H_true: torch.Tensor) -> torch.Tensor:
    H_hat_c = H_hat[..., 0] + 1j * H_hat[..., 1]
    H_true_c = H_true[..., 0] + 1j * H_true[..., 1]
    num = torch.mean(torch.abs(H_hat_c - H_true_c) ** 2, dim=(-2, -1))
    den = torch.mean(torch.abs(H_true_c) ** 2, dim=(-2, -1))
    nmse_lin = num / (den + 1e-12)
    return 10 * torch.log10(nmse_lin + 1e-12)


def count_parameters(model: torch.nn.Module) -> int:
    return sum(p.numel() for p in model.parameters() if p.requires_grad)


def evaluate_nmse_vs_snr(model: torch.nn.Module, config: NRConfig,
                         snr_list: list, n_samples_per_snr: int = 200,
                         device: str = "cpu") -> dict:
    model.eval()
    results = {}
    mask = config.dmrs_mask

    with torch.no_grad():
        for snr_db in snr_list:
            nmse_list = []
            rng = np.random.RandomState(int(snr_db * 100))
            for _ in range(n_samples_per_snr):
                X, H, Y = generate_triplet(config, snr_db, "TDL-C", doppler_hz=50.0, rng=rng)

                pilot_input = extract_pilot_features(Y, mask)
                H_target = channel_to_2ch(H)

                inp = torch.from_numpy(pilot_input).unsqueeze(0).to(device)
                H_hat_2ch = model(inp).squeeze(0).cpu().numpy()
                H_hat = channel_from_2ch(H_hat_2ch)

                nmse = compute_nmse(H_hat, H)
                nmse_list.append(nmse)

            results[snr_db] = {
                "mean_nmse": np.mean(nmse_list),
                "std_nmse": np.std(nmse_list),
                "all_nmse": nmse_list
            }

    return results


def evaluate_baselines_vs_snr(config: NRConfig, snr_list: list,
                               n_samples_per_snr: int = 200) -> dict:
    results = {"ls": {}, "ls_interp": {}}
    mask = config.dmrs_mask

    for snr_db in snr_list:
        ls_nmses = []
        lsi_nmses = []
        rng = np.random.RandomState(int(snr_db * 100))
        for _ in range(n_samples_per_snr):
            X, H, Y = generate_triplet(config, snr_db, "TDL-C", doppler_hz=50.0, rng=rng)

            H_ls = ls_estimate(Y, X, mask)
            ls_nmses.append(compute_nmse(H_ls, H))

            H_lsi = ls_interpolate(Y, X, mask)
            lsi_nmses.append(compute_nmse(H_lsi, H))

        results["ls"][snr_db] = {"mean_nmse": np.mean(ls_nmses), "std_nmse": np.std(ls_nmses)}
        results["ls_interp"][snr_db] = {"mean_nmse": np.mean(lsi_nmses), "std_nmse": np.std(lsi_nmses)}

    return results


def train_model(model: torch.nn.Module, train_loader, val_loader,
                n_epochs: int = 50, lr: float = 1e-3,
                device: str = "cpu", print_every: int = 5) -> dict:
    model = model.to(device)
    optimizer = torch.optim.Adam(model.parameters(), lr=lr)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=n_epochs)
    criterion = torch.nn.MSELoss()

    history = {"train_loss": [], "val_nmse": []}

    for epoch in range(n_epochs):
        model.train()
        epoch_loss = 0.0
        n_batches = 0
        for pilot_input, H_target, mask_arr, snr in train_loader:
            pilot_input = pilot_input.to(device)
            H_target = H_target.to(device)

            H_pred = model(pilot_input)
            loss = criterion(H_pred, H_target)

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()

            epoch_loss += loss.item()
            n_batches += 1

        scheduler.step()
        avg_loss = epoch_loss / max(n_batches, 1)
        history["train_loss"].append(avg_loss)

        if (epoch + 1) % print_every == 0 or epoch == 0:
            model.eval()
            val_nmses = []
            with torch.no_grad():
                for pilot_input, H_target, mask_arr, snr in val_loader:
                    pilot_input = pilot_input.to(device)
                    H_target = H_target.to(device)
                    H_pred = model(pilot_input)
                    batch_nmse = compute_nmse_batch(H_pred, H_target)
                    val_nmses.extend(batch_nmse.cpu().numpy().tolist())

            mean_val_nmse = np.mean(val_nmses)
            history["val_nmse"].append(mean_val_nmse)
            print(f"Epoch {epoch+1:3d}/{n_epochs} | Loss: {avg_loss:.6f} | "
                  f"Val NMSE: {mean_val_nmse:.2f} dB | LR: {scheduler.get_last_lr()[0]:.2e}")

    return history
