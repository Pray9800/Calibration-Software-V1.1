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
    "S2中心16x+高压4x（线性版）",
    "S2中心8x+高压4x（线性版）",
]
# PROFILES 字段说明：
#   family=alt: 原始通道值、A~D 边界按 k 均值成比例（0.6~1.4）
#   family=opt: 逐通道归一化、A~D 绝对边界 20~200
#   weight  eq：各数据点等权；tol：按压力容差反比例加权；
#           tol10：同 tol 的 10% 起算曲线做底权，再按“误差/容差”超差频段迭代放大
#   center  中心文件的权重倍数；配合 weight 使用，如 1、8、16
#   high    none：高压段不额外加权；fixed4：>=150N 固定 4 倍；
#           linear4：150~220N 从 1 倍线性增加到 4 倍
#   bounds  仅 family=alt 使用：(下限系数, 上限系数)，乘以 k 均值得到实际 A~D 边界
PROFILES = {
    "S2联合": dict(family="alt", weight="eq", center=1, high="none", bounds=(0.6, 1.4)),
    "S2中心8x": dict(family="alt", weight="eq", center=8, high="none", bounds=(0.6, 1.4)),
    "S2容差优先": dict(family="alt", weight="tol10", center=8, high="none", bounds=(0.6, 1.4)),
    "S2中心16x+高压4x（线性版）": dict(family="opt", weight="tol", center=16, high="linear4"),
    "S2中心8x+高压4x（线性版）": dict(family="opt", weight="tol", center=8, high="linear4"),
}

# 容差验收表（与 index.html 的 DATA["_tol"] 同值，10N 一段）
TOL = np.array([3, 3, 3, 3.45, 4.3, 5.1, 5.83, 6.51, 7.13, 7.68, 8.18, 8.63,
                9.01, 9.33, 9.6, 9.8, 9.95, 10.04, 10.07, 10.04, 10.25, 10.75])


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 获取压力数据的有效起止范围
# Input: rows: 数据行；end_thresh: 结束压力阈值
# Output: 有效数据的起止下标
# Return: (起始下标, 结束下标)，按左闭右开处理 
# *******************************************************
def _get_range(rows, end_thresh):
    rs, re = 0, len(rows) - 1
    for i in range(len(rows) - 2):
        if rows[i][0] > 0 and rows[i + 1][0] > 0 and rows[i + 2][0] > 0:
            rs = i
            break
    for i in range(len(rows)):
        if rows[i][0] > end_thresh:   #超过阈值
            re = i
            break
    return rs, re


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 提取四个通道并扣除初始值
# Input: rows: 数据行；clum: 通道起始列
# Output: 压力数组和四通道数据
# Return: (压力数组, 通道数组)
# *******************************************************
def _channels(rows, clum):
    rs, re = _get_range(rows, 220.5)
    rp = np.asarray(rows[rs:re, 0], dtype=float)
    chs = np.asarray(rows[:, clum:clum + 4].T, dtype=float).copy() #复制提取1列到4列
    chs -= chs[:, :1]   #初始变化
    return rp, chs[:, rs:re]  #上传有效列


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 计算单组数据的 k 值
# Input: rows: 数据行；clum: 通道起始列
# Output: 单组数据的 k 值
# Return: 浮点数
# *******************************************************
def k_one(rows, clum):
    rp, chs = _channels(rows, clum) 
    s = chs.sum(axis=0)  #累加通道n 所有数值 比如一个压力值对于所有应变片参数值
    sxx = float(s @ s)   #平方 做分母
    return float(s @ rp) / sxx if sxx > 0 else 0.0   #计算k值 公式 k = sum(s[i]*rp[i])/sum(s[i]^2)  s[i]为通道n的应变片参数值  rp[i]为压力值  最小二乘法


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 计算多组数据 k 值的平均值
# Input: files: 多组数据；clum: 通道起始列
# Output: k 值平均值
# Return: 浮点数
# *******************************************************
def k_mean(files, clum):
    return sum(k_one(f, clum) for f in files) / len(files)  #计算平均值{压力值 } = k {应变片总读数 } s


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 接收普通行列表并计算单组 k 值
# Input: rows: 数据行列表；clum: 通道起始列
# Output: 单组数据的 k 值
# Return: 浮点数
# *******************************************************
def k_one_rows(rows, clum):
    return k_one(np.asarray(rows, dtype=float), int(clum))  #不参与拟合参与后续的显示



# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 使用 S1 方案拟合 A~F 参数
# Input: files: 多组数据；center_idx: 中心数据下标；clum: 通道起始列
# Output: A~F 参数
# Return: 六个参数组成的列表
# *******************************************************
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


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 按方案配置计算各数据点的拟合权重
# Input: p: 压力数组；tags: 中心数据标记；prof: 方案配置
# Output: 权重数组
# Return: 权重数组
#tol10: 10N以下按 10% 容差，10~200N 按 5~10% 容差线性过渡，200N以上按 5% 容差（仅作迭代底权）
#tol: 30N以下按 10% 容差，30~200N 按 5~10% 容差线性过渡，200N以上按 5% 容差
#"fixed4": 150N以上固定 4 倍权重 
#"linear4: 150~220N 按 1~4 倍线性过渡
# *******************************************************
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


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 整理拟合数据并按需归一化通道
# Input: files: 多组数据；center_idx: 中心数据下标；clum: 通道起始列；normalize: 是否归一化
# Output: 通道、压力、中心标记和归一化范围
# Return: (X, y, tags, ranges)
# *******************************************************
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


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 根据方案生成 E、F 和 A~D 的初始值
# Input: 拟合数据、方案配置及 k 均值
# Output: E、F 初值和 A~D 初值
# Return: (ef, abcd)
# *******************************************************
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


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 按指定方案迭代拟合 A~F 参数
# Input: files: 多组数据；center_idx: 中心下标；clum: 通道起始列；prof: 方案配置
# Output: 拟合后的 A~F 参数
# Return: 六个参数组成的列表
# *******************************************************
def _solve(X, y, w, ef, abcd, lo, hi, max_outer):
    ef = np.asarray(ef, dtype=float)
    for _ in range(max_outer):   #迭代次数
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
    return ef, abcd


def _band_ratios(files, clum, params):
    """每 10N 一段（b=3..19 即 30~200N）在各文件中的最大 RMS/容差。"""
    a, b, c, d, e, f = params
    ratio = np.zeros(len(TOL))
    for rows in files:
        rp, chs = _channels(rows, clum)
        t = a * chs[0] + b * chs[1] + c * chs[2] + d * chs[3]
        err = rp - (t - e * t * t - f * t)
        band = np.clip((rp / 10).astype(int), 0, len(TOL) - 1)
        for k in range(3, 20):
            m = band == k
            if m.any():
                ratio[k] = max(ratio[k], float(np.sqrt(np.mean(err[m] ** 2))) / TOL[k])
    return ratio


def fit_profile(files, center_idx, clum, prof, max_outer=None):
    normalize = prof["family"] == "opt"  #是否归一化"opt" 才做归一化
    X, y, tags, ranges = _dataset(files, center_idx, clum, normalize)  #数据合并
    w = _weights(y, tags, prof)  # 计算权重 上文配置
    km = k_mean(files, clum)     # 计算均值（四通道累加的k）
    ef, abcd = _initial_ef(files, center_idx, clum, X, y, prof, km)  #   alt 家族直接拿 fit_s1 的结果起步；opt 家族用 55.0 和中心文件拟合结果起步
    if prof["family"] == "alt":
        lo = np.full(4, prof["bounds"][0] * km)  #范围
        hi = np.full(4, prof["bounds"][1] * km)
        max_outer = max_outer or 200  #200次迭代 
    else:
        lo = np.full(4, 20.0)   
        hi = np.full(4, 200.0)
        max_outer = max_outer or 3000  
    ef = np.asarray(ef, dtype=float)
    if prof["weight"] != "tol10":   #其余方案单轮求解，权重固定
        ef, abcd = _solve(X, y, w, ef, abcd, lo, hi, max_outer)
        out = abcd / ranges if normalize else abcd
        return [float(v) for v in (*out, ef[0], ef[1])]
    # tol10：外层按“误差/容差”超差频段放大权重，最多 6 轮，保留最好一轮
    band = np.clip((y / 10).astype(int), 0, len(TOL) - 1)
    g = np.ones(len(TOL))
    best, prev = None, None
    for _ in range(6):
        ef, abcd = _solve(X, y, w * g[band], ef, abcd, lo, hi, max_outer)
        out = abcd / ranges if normalize else abcd
        params = [float(v) for v in (*out, ef[0], ef[1])]
        ratio = _band_ratios(files, clum, params)
        mx = float(np.max(ratio[3:20]))
        if best is None or mx < best[1]:
            best = (params, mx)
        if mx <= 1.0 or (prev is not None and (prev - mx) / prev < 0.02):
            break
        prev = mx
        upd = np.where(ratio > 1.0, np.minimum(g * 1.5, 16.0), g)
        if np.allclose(upd, g):
            break
        g = upd
    return best[0]


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 运行全部标定方案
# Input: files: 多组数据；center_idx: 中心下标；clum: 通道起始列
# Output: 各方案的 A~F 参数
# Return: 以方案名为键的字典
# *******************************************************
def fit_all(files, center_idx, clum):
    files = [np.asarray(f, dtype=float) for f in files]
    out = {"S1": fit_s1(files, center_idx, clum)}
    for name in SCHEME_ORDER[1:]:
        out[name] = fit_profile(files, center_idx, clum, PROFILES[name])
    return out


# *******************************************************
# Author: PAN       Version: V1.1       Date: 2026/11/23
# Description: 读取 CSV 并转换为数值数组
# Input: path: CSV 文件路径
# Output: 文件中的数据行
# Return: NumPy 浮点数组
# *******************************************************
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
