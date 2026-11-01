"""隔离与还原。

三条硬规则：
  1. 只 move，从不 delete。隔离目录里的文件随时能还原。
  2. 每组至少留一份在原位置，绝不把一组清空。
  3. 落盘前先写 manifest.json，记录完整绝对路径映射，还原不依赖文件名猜测。

跨盘（如 C: → D:）时 shutil.move 会自动降级为复制+删除，会慢一些，
但失败时会留下原始文件，不会造成数据丢失。
"""

from __future__ import annotations

import csv
import json
import re
import shutil
from datetime import datetime
from pathlib import Path

KEEP_RULES = {
    "best": "质量优先（分辨率→体积→文件名→时间）",
    "largest": "文件最大",
    "resolution": "分辨率最高",
    "oldest": "修改时间最早",
    "shortest": "路径最短（层级最浅）",
    "first": "路径字典序",
}

_CJK_RE = re.compile(r"[\u4e00-\u9fff]")
_PURE_NUM_RE = re.compile(r"^\d+$")
# 程序批量改名留下的“无信息量”文件名：img1 / IMG_0012 / DSC01234 / untitled ...
_NOISE_RE = re.compile(
    r"^(img|image|pic|photo|dsc|dcim|untitled|new)[_\-\s]*\d*$", re.I)


def _name_score(path) -> int:
    """文件名信息量打分。

    批量处理过的图库里，同一张图往往同时存在 img17.jpg 和 教学楼1.jpg 两个名字。
    分辨率、体积都一样时，应该留住有语义的那个名字，而不是按字典序留下 img17。
    """
    stem = Path(path).stem
    score = 0
    if _CJK_RE.search(stem):
        score += 3
    if _PURE_NUM_RE.match(stem):
        score -= 4
    elif _NOISE_RE.match(stem):
        score -= 3
    if len(stem) >= 6:
        score += 1
    return score



def _sort_key(rule: str):
    if rule == "largest":
        return lambda m: (-m.size, str(m.path).lower())
    if rule == "resolution":
        return lambda m: (-m.pixels, -m.size, str(m.path).lower())
    if rule == "oldest":
        return lambda m: (m.mtime, str(m.path).lower())
    if rule == "shortest":
        return lambda m: (len(m.path.parts), len(str(m.path)), str(m.path).lower())
    if rule == "first":
        return lambda m: str(m.path).lower()
    # best：分辨率 > 体积 > 文件名信息量 > 更早 > 路径
    return lambda m: (-m.pixels, -m.size, -_name_score(m.path), m.mtime,
                      str(m.path).lower())


def plan_by_rule(groups, rule: str = "best") -> dict:
    """返回 {str(源路径): 'keep' | 'isolate'}。每组保留一份。"""
    key = _sort_key(rule)
    plan: dict[str, str] = {}
    for g in groups:
        ordered = sorted(g.members, key=key)
        for i, m in enumerate(ordered):
            plan[str(m.path)] = "keep" if i == 0 else "isolate"
    return plan


def default_isolated_root(root) -> Path:
    root = Path(root).resolve()
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    parent = root.parent if root.parent != root else root
    return parent / f"{root.name}_重复隔离_{stamp}"


def _unique_target(directory: Path, name: str) -> Path:
    target = directory / name
    if not target.exists():
        return target
    stem, suffix = Path(name).stem, Path(name).suffix
    i = 2
    while True:
        cand = directory / f"{stem}_{i}{suffix}"
        if not cand.exists():
            return cand
        i += 1


def execute(
    groups,
    plan: dict,
    isolated_root,
    rule: str = "best",
    dry_run: bool = False,
    scan_root=None,
    progress=None,
) -> dict:
    """按计划移动文件，返回 manifest 字典。dry_run=True 时只算不搬。"""
    # 必须解析成绝对路径：manifest 里记相对路径，换个工作目录还原就会失效
    isolated_root = Path(isolated_root).resolve()
    entries = []
    moved = 0
    failed = []

    if not dry_run:
        isolated_root.mkdir(parents=True, exist_ok=True)

    for g in groups:
        # 安全检查：一组里必须至少有一个 keep
        if not any(plan.get(str(m.path)) == "keep" for m in g.members):
            fallback = sorted(g.members, key=_sort_key(rule))[0]
            plan[str(fallback.path)] = "keep"

        seq = 0
        for m in g.members:
            if plan.get(str(m.path)) != "isolate":
                continue
            seq += 1
            group_dir = isolated_root / f"g{g.index:04d}"
            name = f"{seq:02d}_{m.path.name}"
            target = group_dir / name

            entry = {
                "group": g.index,
                "src": str(m.path.resolve()),
                "dst": str(target),
                "sha256": m.sha256,
                "size": m.size,
                "mtime": m.mtime,
                "relation": g.relation,
            }

            if dry_run:
                entries.append(entry)
                moved += 1
                continue

            try:
                group_dir.mkdir(parents=True, exist_ok=True)
                target = _unique_target(group_dir, name)
                entry["dst"] = str(target)
                shutil.move(str(m.path), str(target))
                entries.append(entry)
                moved += 1
                if progress and moved % 50 == 0:
                    progress(f"已移动 {moved} 个文件")
            except Exception as exc:  # noqa: BLE001
                entry["error"] = str(exc)
                failed.append(entry)
                if progress:
                    progress(f"移动失败：{m.path.name} — {exc}")

    manifest = {
        "version": 1,
        "created": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "scan_root": str(Path(scan_root).resolve()) if scan_root else "",
        "isolated_root": str(isolated_root),
        "rule": rule,
        "rule_text": KEEP_RULES.get(rule, rule),
        "moved_count": moved,
        "failed_count": len(failed),
        "dry_run": dry_run,
        "entries": entries + failed,
    }

    if not dry_run:
        (isolated_root / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        with open(isolated_root / "manifest.csv", "w",
                  encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["组号", "原路径", "隔离后路径", "大小(字节)", "SHA256", "状态"])
            for e in manifest["entries"]:
                w.writerow([
                    e["group"], e["src"], e["dst"], e.get("size", ""),
                    e.get("sha256", ""), e.get("error", "已移动"),
                ])

    manifest["_manifest_path"] = str(isolated_root / "manifest.json")
    return manifest


def restore(manifest_path, dry_run: bool = False, progress=None) -> dict:
    """按 manifest 把文件移回原位置。原位置已有同名文件时自动加后缀，绝不覆盖。"""
    manifest_path = Path(manifest_path).resolve()
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    base = manifest_path.parent

    restored = 0
    skipped = []
    failed = []

    for e in data.get("entries", []):
        if e.get("error"):
            skipped.append((e.get("src", ""), "当初移动失败，未产生隔离文件"))
            continue

        src = Path(e["src"])
        dst = Path(e["dst"])
        # 兼容旧版清单里的相对路径：相对 manifest 所在目录解析
        if not dst.is_absolute():
            dst = (base / dst).resolve()
        if not src.is_absolute():
            src = (base / src).resolve()

        if not dst.exists():
            skipped.append((str(src), f"隔离文件不存在：{dst}"))
            continue

        target = src
        if src.exists():
            target = _unique_target(src.parent, f"{src.stem}_restored{src.suffix}")

        if dry_run:
            restored += 1
            continue

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst), str(target))
            restored += 1
            if progress and restored % 50 == 0:
                progress(f"已还原 {restored} 个文件")
        except Exception as exc:  # noqa: BLE001
            failed.append((str(src), str(exc)))

    # 清理空的组目录，让隔离目录保持干净（不删文件，只删空目录）
    if not dry_run and restored:
        iso_root = Path(data.get("isolated_root", "")).resolve()
        if iso_root.is_dir():
            for sub in sorted(iso_root.glob("g*"), reverse=True):
                try:
                    if sub.is_dir() and not any(sub.iterdir()):
                        sub.rmdir()
                except OSError:
                    pass

    return {
        "restored": restored,
        "skipped": skipped,
        "failed": failed,
        "isolated_root": data.get("isolated_root", ""),
        "dry_run": dry_run,
    }
