"""扫描 + 分组。

两档判定：
  1) 精确档 —— 文件大小相同的组内算 SHA256，哈希一致即"字节完全相同"（改名的纯副本）
  2) 相似档 —— pHash 与 dHash 双双落在阈值内，且宽高比接近，即"同一张图被重新保存过"

相似档用「球状贪心聚类」而不是并查集。原因：感知哈希的距离不满足传递性，
A 像 B、B 像 C 时 A 和 C 可能差很远，用并查集会连成一条长链，最后把一整批
不相干的图判成一组。以种子为中心划球、且不扩散，能杜绝这种链式误判。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

from .fingerprint import aspect_compatible, fingerprints, hamming

IMAGE_EXTS = {
    ".jpg", ".jpeg", ".jpe", ".jfif", ".png", ".bmp", ".gif",
    ".tif", ".tiff", ".webp", ".heic", ".heif", ".avif",
}

EXACT = "exact"
SIMILAR = "similar"


@dataclass
class ImageInfo:
    path: Path
    size: int
    mtime: float
    width: int = 0
    height: int = 0
    phash: int = 0
    dhash: int = 0
    sha256: str = ""
    readable: bool = True
    error: str = ""

    @property
    def pixels(self) -> int:
        return self.width * self.height

    @property
    def rel(self) -> str:
        return str(self.path)


@dataclass
class DupGroup:
    index: int
    members: list  # list[ImageInfo]
    relation: str  # EXACT / SIMILAR
    max_distance: int = 0
    exact_subgroups: int = 1

    @property
    def extra_count(self) -> int:
        return max(0, len(self.members) - 1)

    @property
    def reclaim_bytes(self) -> float:
        sizes = sorted((m.size for m in self.members), reverse=True)
        return float(sum(sizes[1:])) if len(sizes) > 1 else 0.0


@dataclass
class ScanResult:
    root: Path
    images: list = field(default_factory=list)
    groups: list = field(default_factory=list)
    unreadable: list = field(default_factory=list)
    elapsed: float = 0.0

    @property
    def extra_copies(self) -> int:
        return sum(g.extra_count for g in self.groups)

    @property
    def reclaim_bytes(self) -> float:
        return sum(g.reclaim_bytes for g in self.groups)


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            block = fh.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def collect(root: Path, skip_dirs=(), progress=None) -> list:
    """递归收集图片文件。skip_dirs 里的目录会被跳过（避免把隔离目录再扫进来）。"""
    rootskip = [str(Path(p).resolve()).lower() for p in skip_dirs if p]
    found = []
    for p in root.rglob("*"):
        try:
            if not p.is_file():
                continue
        except OSError:
            continue
        if p.suffix.lower() not in IMAGE_EXTS:
            continue
        if rootskip and any(str(p.resolve()).lower().startswith(s) for s in rootskip):
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        found.append(ImageInfo(path=p, size=st.st_size, mtime=st.st_mtime))
    if progress:
        progress(f"扫描到 {len(found)} 个图片文件")
    return found


def _exact_groups(images: list, progress=None) -> list:
    """按大小预筛后算 SHA256。大小唯一的文件直接排除，不浪费 IO。"""
    by_size: dict[int, list] = {}
    for im in images:
        by_size.setdefault(im.size, []).append(im)

    candidates = [im for bucket in by_size.values() if len(bucket) > 1 for im in bucket]
    if progress:
        progress(f"大小相同的候选 {len(candidates)} 个，其余 {len(images) - len(candidates)} 个已排除")

    by_hash: dict[str, list] = {}
    for idx, im in enumerate(candidates, 1):
        try:
            im.sha256 = _sha256(im.path)
        except OSError as exc:
            im.readable = False
            im.error = f"读取失败: {exc}"
            continue
        by_hash.setdefault(im.sha256, []).append(im)
        if progress and idx % 200 == 0:
            progress(f"哈希计算 {idx}/{len(candidates)}")

    return [bucket for bucket in by_hash.values() if len(bucket) > 1]


def _cluster_similar(units: list, threshold: int, progress=None) -> list:
    """球状贪心聚类。units 是 list[list[ImageInfo]]，每个单元内部已确认完全相同。

    每个单元用一个代表指纹参与比对；命中后整单元并入，且该单元不再充当种子。
    """
    if len(units) < 2:
        return []

    reps = [u[0] for u in units]
    ph = [r.phash for r in reps]
    dh = [r.dhash for r in reps]

    import numpy as np

    n = len(units)
    ph_arr = np.array(ph, dtype=np.uint64)
    dh_arr = np.array(dh, dtype=np.uint64)

    dp = hamming(ph_arr, ph_arr)
    dd = hamming(dh_arr, dh_arr)
    hit = (dp <= threshold) & (dd <= threshold)
    np.fill_diagonal(hit, False)

    taken = [False] * n
    merged = []

    for i in range(n):
        if taken[i]:
            continue
        members = list(units[i])
        taken[i] = True
        group_idx = [i]
        for j in range(n):
            if taken[j] or not hit[i, j]:
                continue
            if not aspect_compatible(
                reps[i].width, reps[i].height, reps[j].width, reps[j].height
            ):
                continue
            members.extend(units[j])
            taken[j] = True
            group_idx.append(j)
        if len(group_idx) > 1:
            maxd = int(max(dp[i, j] for j in group_idx))
            merged.append((members, maxd, len(group_idx)))
        else:
            merged.append((members, 0, 1))

    if progress:
        progress(f"相似聚类完成，命中 {sum(1 for m in merged if m[2] > 1)} 组")
    return merged


def scan(
    root: Path,
    threshold: int = 6,
    mode: str = "both",
    skip_dirs=(),
    progress=None,
) -> ScanResult:
    import time

    started = time.time()
    root = Path(root).resolve()
    result = ScanResult(root=root)

    images = collect(root, skip_dirs=skip_dirs, progress=progress)
    result.images = images
    if not images:
        result.elapsed = time.time() - started
        return result

    # ---- 精确档 ----
    exact_buckets = _exact_groups(images, progress=progress)
    if progress:
        progress(f"字节级完全相同的组：{len(exact_buckets)} 组")

    if mode == "exact":
        groups = []
        for i, bucket in enumerate(exact_buckets, 1):
            bucket.sort(key=lambda m: (-m.pixels, -m.size, m.mtime, str(m.path).lower()))
            groups.append(DupGroup(i, bucket, EXACT, 0, 1))
        result.groups = groups
        result.elapsed = time.time() - started
        return result

    # ---- 相似档：所有图都要解码 ----
    ok_count = 0
    for idx, im in enumerate(images, 1):
        if not im.readable:
            continue
        try:
            im.phash, im.dhash, im.width, im.height = fingerprints(im.path)
            ok_count += 1
        except Exception as exc:  # noqa: BLE001
            im.readable = False
            im.error = f"解码失败: {exc}"
            result.unreadable.append(im)
        if progress and idx % 100 == 0:
            progress(f"指纹计算 {idx}/{len(images)}")

    # 把精确组变成单元，剩下的图各自成单元
    unit_of = {}
    units = []
    for bucket in exact_buckets:
        units.append(list(bucket))
        for m in bucket:
            unit_of[str(m.path)] = len(units) - 1

    for im in images:
        if not im.readable:
            continue
        if str(im.path) in unit_of:
            continue
        units.append([im])
        unit_of[str(im.path)] = len(units) - 1

    clusters = _cluster_similar(units, threshold, progress=progress)

    groups = []
    for members, maxd, subunit_count in clusters:
        if len(members) < 2:
            continue
        # 组内关系类型：只要出现过不同 SHA256，就是相似而非精确
        shas = {m.sha256 for m in members if m.sha256}
        relation = EXACT if len(shas) <= 1 and subunit_count == 1 else SIMILAR
        members.sort(key=lambda m: (-m.pixels, -m.size, m.mtime, str(m.path).lower()))
        groups.append(DupGroup(0, members, relation, maxd, subunit_count))

    # 纯精确组（没被相似档捞到的）也要保留
    grouped_paths = {str(m.path) for g in groups for m in g.members}
    for bucket in exact_buckets:
        if any(str(m.path) not in grouped_paths for m in bucket):
            bucket.sort(key=lambda m: (-m.pixels, -m.size, m.mtime, str(m.path).lower()))
            groups.append(DupGroup(0, bucket, EXACT, 0, 1))

    groups.sort(key=lambda g: (-len(g.members), -(g.reclaim_bytes)))
    for i, g in enumerate(groups, 1):
        g.index = i

    result.groups = groups
    result.elapsed = time.time() - started
    if progress:
        progress(f"完成：{len(groups)} 组，{result.extra_copies} 个多余副本")
    return result
