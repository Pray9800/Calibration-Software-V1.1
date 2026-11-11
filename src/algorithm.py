"""标定参数 A~F 求解。

求解逻辑的唯一实现：前端 index.html 只负责画图、分段 RMS 和容差校验，
所有方案都走 fit_all()。数据约定与 CSV 一致：
    rows[i] = [压力, 通道0..通道7, ...]
    clum    = 1(左侧 B~E) 或 5(右侧 F~I)，取 rows[i][clum:clum+4]

新增/调整方案只改 PROFILES 表；例如中心 8x 改 10x：把该方案的 center 改成 10。
"""
from __future__ import annotations

import numpy as np

SCHEME_ORDER = [
    "S1",
    "S2联合",
    "S2中心8x",
    "S2容差优先",
    "S2中心优先",
    "S2中心16x+高压4x",
    "S2中心16x+高压4x（线性版）",
    "S2中心8x+高压4x（线性版）",
]

# family=alt: 原始通道值、A~D 边界按 k 均值成比例（0.6~1.4，中心优先 0.3~3.0）
# family=opt: 逐通道归一化、A~D 绝对边界 20~200
PROFILES = {
    "S2联合": dict(family="alt", weight="eq", center=1, high="none", bounds=(0.6, 1.4)),
    "S2中心8x": dict(family="alt", weight="eq", center=8, high="none", bounds=(0.6, 1.4)),
    "S2容差优先": dict(family="alt", weight="tol10", center=8, high="none", bounds=(0.6, 1.4)),
    "S2中心优先": dict(family="alt", weight="eq", center=32, high="none", bounds=(0.3, 3.0)),
    "S2中心16x+高压4x": dict(family="opt", weight="tol", center=16, high="fixed4"),
    "S2中心16x+高压4x（线性版）": dict(family="opt", weight="tol", center=16, high="linear4"),
    "S2中心8x+高压4x（线性版）": dict(family="opt", weight="tol", center=8, high="linear4"),
}


def _get_range(rows, end_thresh):
    rs, re = 0, len(rows) - 1
    for i in range(len(rows) - 2):
        if rows[i][0] > 0 and rows[i + 1][0] > 0 and rows[i + 2][0] > 0:
            rs = i
            break
    for i in range(len(rows)):
        if rows[i][0] > end_thresh:
            re = i
            break
    return rs, re


def _channels(rows, clum):
    rs, re = _get_range(rows, 220.5)
    rp = np.asarray(rows[rs:re, 0], dtype=float)
    chs = np.asarray(rows[:, clum:clum + 4].T, dtype=float).copy()
    chs -= chs[:, :1]
    return rp, chs[:, rs:re]


def k_one(rows, clum):
    rp, chs = _channels(rows, clum)
    s = chs.sum(axis=0)
    sxx = float(s @ s)
    return float(s @ rp) / sxx if sxx > 0 else 0.0


def k_mean(files, clum):
    return sum(k_one(f, clum) for f in files) / len(files)


def k_one_rows(rows, clum):
    return k_one(np.asarray(rows, dtype=float), int(clum))


def fit_s1(files, center_idx, clum):
    a, b, c, d = [k_one(f, clum) for f in files][:4]
    rows = files[center_idx]
    rs, re = _get_range(rows, 200.0)
    chs = np.asarray(rows[:, clum:clum + 4].T, dtype=float).copy()
    chs -= chs[:, :1]
    rp = np.asarray(rows[rs:re, 0], dtype=float)
    t = a * chs[0, rs:re] + b * chs[1, rs:re] + c * chs[2, rs:re] + d * chs[3, rs:re]
    err = t - rp
    s11 = float(np.sum(t ** 4))
    s12 = float(np.sum(t ** 3))
    s22 = float(np.sum(t ** 2))
    sy1 = float(np.sum(t ** 2 * err))
    sy2 = float(np.sum(t * err))
    det = s11 * s22 - s12 * s12
    if abs(det) <= 1e-24:
        return [a, b, c, d, 0.0, 0.0]
    return [a, b, c, d, (sy1 * s22 - sy2 * s12) / det,
            (s11 * sy2 - s12 * sy1) / det]


def _weights(p, tags, prof):
    w = np.ones_like(p)
    if prof["weight"] == "tol10":
        t = np.maximum(p, 1.0)
        frac = np.clip((t - 10.0) / 190.0, 0.0, 1.0)
        tol = t * (0.10 - 0.05 * frac)
        w = 1.0 / (tol * tol)
    elif prof["weight"] == "tol":
        t = np.maximum(p, 30.0)
        frac = np.clip((t - 30.0) / 170.0, 0.0, 1.0)
        tol = t * (0.10 - 0.05 * frac)
        w = 1.0 / (tol * tol)
    if prof["high"] == "fixed4":
        w = w * np.where(p >= 150.0, 4.0, 1.0)
    elif prof["high"] == "linear4":
        w = w * (1.0 + 3.0 * np.clip((p - 150.0) / 70.0, 0.0, 1.0))
    return w * np.where(tags, float(prof["center"]), 1.0)


def _dataset(files, center_idx, clum, normalize):
    ranges = np.ones(4)
    if normalize:
        for rows in files:
            _, chs = _channels(rows, clum)
            ranges = np.maximum(ranges, chs.max(axis=1))
        ranges[ranges <= 0] = 1.0
    xs, ys, tags = [], [], []
    for fi, rows in enumerate(files):
        rp, chs = _channels(rows, clum)
        xs.append(chs / ranges[:, None])
        ys.append(rp)
        tags.append(np.full(len(rp), fi == center_idx))
    return np.hstack(xs), np.concatenate(ys), np.concatenate(tags), ranges


def _initial_ef(files, center_idx, clum, X, y, prof, km):
    if prof["family"] == "alt":
        s1 = fit_s1(files, center_idx, clum)
        return np.array([s1[4], s1[5]]), np.full(4, km)
    b0 = 55.0
    rp_c, chs = _channels(files[center_idx], clum)
    rng = np.maximum(
        np.max(np.hstack([_channels(f, clum)[1] for f in files]), axis=1), 1e-12)
    t0 = b0 * (chs / rng[:, None]).sum(axis=0)
    p = rp_c
    m = p <= 200.0
    t0, p = t0[m], p[m]
    s11 = float(np.sum(t0 ** 4))
    s12 = float(np.sum(t0 ** 3))
    s22 = float(np.sum(t0 ** 2))
    sy1 = float(np.sum(t0 ** 2 * (t0 - p)))
    sy2 = float(np.sum(t0 * (t0 - p)))
    det = s11 * s22 - s12 * s12
    if abs(det) > 0:
        return np.array([(sy1 * s22 - sy2 * s12) / det,
                         (s11 * sy2 - s12 * sy1) / det]), np.full(4, b0)
    return np.array([-0.001, 0.1]), np.full(4, b0)


def fit_profile(files, center_idx, clum, prof, max_outer=None):
    normalize = prof["family"] == "opt"
    X, y, tags, ranges = _dataset(files, center_idx, clum, normalize)
    w = _weights(y, tags, prof)
    km = k_mean(files, clum)
    ef, abcd = _initial_ef(files, center_idx, clum, X, y, prof, km)
    if prof["family"] == "alt":
        lo = np.full(4, prof["bounds"][0] * km)
        hi = np.full(4, prof["bounds"][1] * km)
        max_outer = max_outer or 200
    else:
        lo = np.full(4, 20.0)
        hi = np.full(4, 200.0)
        max_outer = max_outer or 3000
    ef = np.asarray(ef, dtype=float)
    for _ in range(max_outer):
        E, F = ef
        for _ in range(40):
            t = abcd @ X
            fp = 1.0 - 2.0 * E * t - F
            r = t - E * t * t - F * t - y
            g = X @ (w * r * fp)
            H = (X * (w * fp * fp)) @ X.T
            try:
                d = np.linalg.solve(H, g)
            except np.linalg.LinAlgError:
                d = np.zeros(4)
            new = np.clip(abcd - d, lo, hi)
            mr = float(np.max(np.abs(new - abcd) / (np.abs(abcd) + 1e-12)))
            abcd = new
            if mr < 1e-12:
                break
        t = abcd @ X
        t2 = t * t
        r = t - y
        q11 = float(np.sum(w * t2 * t2))
        q12 = float(np.sum(w * t2 * t))
        q22 = float(np.sum(w * t2))
        b1 = float(np.sum(w * t2 * r))
        b2 = float(np.sum(w * t * r))
        det = q11 * q22 - q12 * q12
        ne, nf = ef
        if abs(det) > 1e-24:
            ne = (b1 * q22 - b2 * q12) / det
            nf = (q11 * b2 - q12 * b1) / det
        dd = max(abs(ne - ef[0]) / (abs(ef[0]) + 1e-12),
                 abs(nf - ef[1]) / (abs(ef[1]) + 1e-12))
        ef = np.array([ne, nf])
        if dd < 1e-12:
            break
    out = abcd / ranges if normalize else abcd
    return [float(v) for v in (*out, ef[0], ef[1])]


def fit_all(files, center_idx, clum):
    files = [np.asarray(f, dtype=float) for f in files]
    out = {"S1": fit_s1(files, center_idx, clum)}
    for name in SCHEME_ORDER[1:]:
        out[name] = fit_profile(files, center_idx, clum, PROFILES[name])
    return out


def read_csv(path):
    rows = []
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            parts = line.strip().split(",")
            if len(parts) < 6 or parts[0] == "":
                continue
            try:
                # 与前端 parseCSV 一致：空字段按 0 处理（行尾常带一个逗号）
                rows.append([float(x) if x.strip() else 0.0 for x in parts])
            except ValueError:
                continue
    return np.asarray(rows, dtype=float)


if __name__ == "__main__":
    import argparse

    ap = argparse.ArgumentParser(description="对 4 偏 1 中心的 CSV 组求 A~F")
    ap.add_argument("csv", nargs="+")
    ap.add_argument("--center", type=int, default=-1, help="中心文件下标，默认最后一个")
    ap.add_argument("--right", action="store_true", help="取右侧 F~I 通道")
    args = ap.parse_args()
    files = [read_csv(p) for p in args.csv]
    ci = args.center if args.center >= 0 else len(files) - 1
    for name, p in fit_all(files, ci, 5 if args.right else 1).items():
        print(f"{name}: " + " ".join(f"{v:.8g}" for v in p))
