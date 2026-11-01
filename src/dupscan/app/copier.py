"""复制精简：原目录只读，另建一份去重后的完整副本。

与 isolator.py 的根本区别——这里**一个字节都不碰原目录**：
  · 只对源文件做 open(..., 'rb')，绝不 rename / 绝不 delete
  · 新目录里放的是「去重后的全集」：每组留一份 + 所有本来就唯一的图
  · 用 copy2 保留修改时间，方便和原件对照
  · 复制完成立刻比对文件大小，对不上就记为失败，不静默放过
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path

from .isolator import KEEP_RULES, _sort_key, _unique_target


def default_collection_root(source_root) -> Path:
    """默认落在源目录**外面**（同级），避免下次扫描把自己扫进去。"""
    src = Path(source_root).resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M")
    parent = src.parent if src.parent != src else src
    return parent / f"{src.name}_去重精简_{stamp}"


def _check_not_inside(out_root: Path, src_root: Path) -> None:
    out = out_root.resolve()
    src = src_root.resolve()
    if out == src:
        raise ValueError("输出目录不能和扫描目录相同。请换一个位置。")
    if src in out.parents:
        raise ValueError(
            f"输出目录不能放在扫描目录内部（会破坏「原目录不动」的承诺，"
            f"下次扫描也会把它扫进去）。\n  扫描目录：{src}\n  输出目录：{out}"
        )


def plan_keep(result, rule: str = "best") -> tuple[list, set]:
    """返回 (要复制的图片列表, 被跳过的路径集合)。"""
    key = _sort_key(rule)
    skip: set[str] = set()
    for g in result.groups:
        ordered = sorted(g.members, key=key)
        for m in ordered[1:]:
            skip.add(str(m.path))

    kept = [im for im in result.images if str(im.path) not in skip]
    kept.sort(key=lambda x: str(x.path).lower())
    return kept, skip


def collect(
    result,
    out_root,
    rule: str = "best",
    keep_structure: bool = True,
    verify_hash: bool = False,
    progress=None,
) -> dict:
    from .scanner import _sha256  # 延迟导入，避免循环依赖

    src_root = Path(result.root).resolve()
    out_root = Path(out_root).resolve()
    _check_not_inside(out_root, src_root)

    kept, skip = plan_keep(result, rule)

    # ---- 空间检查 ----
    need = sum(im.size for im in kept)
    try:
        out_root.parent.mkdir(parents=True, exist_ok=True)
        free = shutil.disk_usage(out_root.parent).free
    except OSError:
        free = None

    if free is not None and need > free * 0.95:
        raise OSError(
            f"空间不足：需要 {need / 1048576:.1f} MB，"
            f"目标盘可用 {free / 1048576:.1f} MB。请换到空间更大的位置。"
        )

    entries = []
    copied = 0
    failed = []

    for idx, im in enumerate(kept, 1):
        rel = im.path.relative_to(src_root)
        if keep_structure:
            dst = out_root / rel
        else:
            dst = out_root / im.path.name

        entry = {
            "src": str(im.path),
            "dst": str(dst),
            "size": im.size,
            "mtime": im.mtime,
            "sha256": im.sha256,
        }

        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not keep_structure:
                dst = _unique_target(dst.parent, dst.name)
                entry["dst"] = str(dst)

            shutil.copy2(str(im.path), str(dst))

            # 复制后立刻核对大小，对不上就判失败
            if dst.stat().st_size != im.size:
                raise OSError("复制后大小不一致")

            if verify_hash:
                if not im.sha256:
                    im.sha256 = _sha256(im.path)
                if _sha256(dst) != im.sha256:
                    raise OSError("复制后 SHA256 不一致")

            entries.append(entry)
            copied += 1
            if progress and copied % 100 == 0:
                progress(f"已复制 {copied}/{len(kept)}")
        except Exception as exc:  # noqa: BLE001
            entry["error"] = str(exc)
            failed.append(entry)
            if progress:
                progress(f"复制失败：{im.path.name} — {exc}")

    manifest = {
        "version": 1,
        "mode": "copy",
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "source_root": str(src_root),
        "collection_root": str(out_root),
        "rule": rule,
        "rule_text": KEEP_RULES.get(rule, rule),
        "keep_structure": keep_structure,
        "source_count": len(result.images),
        "copied_count": copied,
        "skipped_duplicates": len(skip),
        "failed_count": len(failed),
        "bytes_copied": sum(e["size"] for e in entries),
        "source_untouched": True,
        "entries": entries + failed,
    }

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    with open(out_root / "manifest.csv", "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["原路径", "副本路径", "大小(字节)", "SHA256", "状态"])
        for e in manifest["entries"]:
            w.writerow([e["src"], e["dst"], e["size"], e.get("sha256", ""),
                        e.get("error", "已复制")])

    manifest["_manifest_path"] = str(out_root / "manifest.json")
    return manifest
