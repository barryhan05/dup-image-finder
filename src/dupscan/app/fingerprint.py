"""图片指纹：pHash + dHash，纯 numpy 实现，不依赖 scipy / opencv。

pHash 抓「整体构图」——重新压缩、缩放、轻微裁剪后依然稳定。
dHash 抓「局部梯度」——对亮度变化的方向敏感，能补 pHash 在白底图上的不足。
两个都算、两个都通过阈值才算相似，误判率明显低于单个哈希。
"""

from __future__ import annotations

import numpy as np
from PIL import Image, ImageOps

try:
    import pillow_heif

    pillow_heif.register_heif_opener()
    HEIC_OK = True
except Exception:  # noqa: BLE001
    HEIC_OK = False

# 扫描件动辄上亿像素，关掉解压炸弹保护，否则大图直接报错
Image.MAX_IMAGE_PIXELS = None

HASH_SIZE = 8  # 8x8 = 64 bit


def _dct_matrix(n: int) -> np.ndarray:
    """正交归一化的 DCT-II 变换矩阵。"""
    k = np.arange(n).reshape(-1, 1)
    x = np.arange(n).reshape(1, -1)
    m = np.cos(np.pi * (2.0 * x + 1.0) * k / (2.0 * n))
    m[0, :] /= np.sqrt(2.0)
    return m * np.sqrt(2.0 / n)


_DCT = _dct_matrix(HASH_SIZE * 4)  # 32x32
_POPCOUNT = np.array([bin(i).count("1") for i in range(256)], dtype=np.uint8)


def _flatten_rgb(im: Image.Image) -> Image.Image:
    """EXIF 转正 + 透明背景合成白底 + 转 RGB。透明 PNG 不处理会让哈希剧烈漂移。"""
    try:
        im = ImageOps.exif_transpose(im)
    except Exception:  # noqa: BLE001
        pass

    if im.mode in ("RGBA", "LA", "PA") or (im.mode == "P" and "transparency" in im.info):
        im = im.convert("RGBA")
        bg = Image.new("RGB", im.size, (255, 255, 255))
        bg.paste(im, mask=im.split()[-1])
        return bg
    return im.convert("RGB")


def fingerprints(path, hash_size: int = HASH_SIZE):
    """返回 (pHash, dHash, 宽, 高)。图片无法解码时抛异常，由调用方捕获。"""
    with Image.open(path) as im:
        im.load()
        width, height = im.size
        gray = _flatten_rgb(im).convert("L")

        # ---- pHash ----
        side = hash_size * 4
        small = gray.resize((side, side), Image.Resampling.LANCZOS)
        px = np.asarray(small, dtype=np.float64)
        coef = _DCT @ px @ _DCT.T
        low = coef[:hash_size, :hash_size].reshape(-1)
        thresh = np.median(low[1:])  # 排除 DC 分量
        ph = 0
        for bit in low > thresh:
            ph = (ph << 1) | int(bit)

        # ---- dHash ----
        dw = gray.resize((hash_size + 1, hash_size), Image.Resampling.LANCZOS)
        dp = np.asarray(dw, dtype=np.int16)
        dh = 0
        for bit in (dp[:, 1:] > dp[:, :-1]).reshape(-1):
            dh = (dh << 1) | int(bit)

    return ph, dh, width, height


def hamming(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """a:(M,) uint64, b:(N,) uint64 -> (M,N) uint8 汉明距离矩阵。"""
    x = a[:, None] ^ b[None, :]
    raw = x.view(np.uint8).reshape(x.shape[0], x.shape[1], 8)
    return _POPCOUNT[raw].sum(axis=2, dtype=np.uint8)


def aspect_compatible(w1: int, h1: int, w2: int, h2: int, tolerance: float = 0.12) -> bool:
    """宽高比差异超过 tolerance 直接判为不同图。

    白底证件照、纯色截图是 pHash 的重灾区——大量纯白让哈希趋同，
    靠宽高比这一刀能挡掉相当一部分跨版式的误判。
    """
    if not (w1 and h1 and w2 and h2):
        return False
    a1, a2 = w1 / h1, w2 / h2
    return abs(a1 - a2) / max(a1, a2) <= tolerance
