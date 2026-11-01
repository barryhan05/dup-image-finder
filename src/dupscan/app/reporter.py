"""报告生成：CSV 清单 + 并排缩略图 HTML。

HTML 用 file:// 引用原图，不复制不转码——报告很小，也不会在目标机上多出一份
需要管理的敏感副本。封面与 logo 以 base64 内嵌，保证报告自身完整可读。
"""

from __future__ import annotations

import base64
import csv
import html
import io
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .scanner import EXACT, ScanResult


def _fmt_size(n: float) -> str:
    if n < 1024:
        return f"{int(n)} B"
    for unit in ("KB", "MB", "GB"):
        n /= 1024
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}"
    return f"{n:.1f} {unit}"


def _file_uri(p: Path) -> str:
    try:
        return p.resolve().as_uri()
    except ValueError:
        return "file:///" + quote(str(p).replace("\\", "/"), safe="/:")


def _rel_dir(p: Path, root: Path) -> str:
    try:
        parent, base = p.parent.resolve(), root.resolve()
        if parent == base:
            return "."
        return str(parent.relative_to(base))
    except (ValueError, OSError):
        return str(p.parent)


def _embed(path, max_w: int, quality: int = 76, keep_alpha: bool = False) -> str:
    if not path:
        return ""
    try:
        from PIL import Image

        with Image.open(path) as im:
            if keep_alpha:
                # logo 类素材一律走 PNG，避免透明底被 RGB 化后出现黑块
                im = im.convert("RGBA")
                fmt, mime = "PNG", "image/png"
            else:
                im = im.convert("RGB")
                fmt, mime = "JPEG", "image/jpeg"

            if im.width > max_w:
                h = max(1, round(im.height * max_w / im.width))
                im = im.resize((max_w, h), Image.Resampling.LANCZOS)

            buf = io.BytesIO()
            if fmt == "PNG":
                im.save(buf, fmt, optimize=True)
            else:
                im.save(buf, fmt, quality=quality, optimize=True, progressive=True)
        return f"data:{mime};base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:  # noqa: BLE001
        return ""


def write_csv(
    result: ScanResult,
    out_dir: Path,
    decisions: dict | None = None,
    skip_label: str = "移入隔离",
) -> Path:
    from .isolator import plan_by_rule

    plan = decisions if decisions is not None else plan_by_rule(result.groups, "best")
    path = out_dir / "duplicates.csv"

    with open(path, "w", encoding="utf-8-sig", newline="") as fh:
        w = csv.writer(fh)
        w.writerow([
            "组号", "组内序号", "动作", "文件名", "所在目录", "完整路径",
            "大小(字节)", "分辨率", "修改时间", "SHA256", "判定", "组内最大距离",
        ])
        for g in result.groups:
            for seq, m in enumerate(g.members, 1):
                act = plan.get(str(m.path), "keep" if seq == 1 else "isolate")
                w.writerow([
                    g.index, seq, "保留" if act == "keep" else skip_label,
                    m.path.name, _rel_dir(m.path, result.root), str(m.path),
                    m.size,
                    f"{m.width}x{m.height}" if m.width else "",
                    datetime.fromtimestamp(m.mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    m.sha256,
                    "字节相同" if g.relation == EXACT else "相似",
                    g.max_distance if g.relation != EXACT else 0,
                ])
    return path


def _dir_summary(result: ScanResult, plan: dict) -> list:
    total = Counter()
    for m in result.images:
        total[_rel_dir(m.path, result.root)] += 1

    keep, iso, iso_bytes = Counter(), Counter(), Counter()
    for g in result.groups:
        for m in g.members:
            d = _rel_dir(m.path, result.root)
            if plan.get(str(m.path)) == "keep":
                keep[d] += 1
            else:
                iso[d] += 1
                iso_bytes[d] += m.size

    return [
        {"dir": d, "total": c, "keep": keep.get(d, 0), "iso": iso.get(d, 0),
         "iso_bytes": iso_bytes.get(d, 0)}
        for d, c in total.most_common()
    ]


CSS = """
:root{
  --bg:#FAF7F3; --card:#FFFFFF; --sunk:#F6F1EB;
  --ink:#322D2B; --ink2:#7C7570; --ink3:#ABA49D;
  --line:#F0EAE3; --line2:#E6DED5;
  --rose:#B2606C; --rose-d:#8F4A56; --rose-bg:#FCF1F2; --rose-ln:#EBCDD2;
  --sage:#6E8C7C; --sage-bg:#F0F5F1; --sage-ln:#CFE0D5;
  --apri:#B9824F; --apri-bg:#FBF3E9; --apri-ln:#EBD5BA;
  --shadow:0 1px 2px rgba(74,54,48,.04), 0 10px 30px rgba(74,54,48,.05);
}
*{box-sizing:border-box;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
body{
  margin:0;background:var(--bg);color:var(--ink);font-size:14px;line-height:1.7;
  font-family:"PingFang SC","HarmonyOS Sans SC","MiSans",-apple-system,BlinkMacSystemFont,
  "Microsoft YaHei UI","Microsoft YaHei",sans-serif;
}
.wrap{max-width:1160px;margin:0 auto;padding:36px 30px 88px}

/* 封面 */
.cover{position:relative;border-radius:26px;overflow:hidden;min-height:284px;
  display:flex;align-items:flex-end;background-size:cover;background-position:center 44%;
  background-color:#3A302C;box-shadow:var(--shadow);margin-bottom:26px}
.cover.plain{background-image:
  radial-gradient(circle at 86% 8%, rgba(178,96,108,.15) 0%, rgba(178,96,108,0) 44%),
  radial-gradient(circle at 68% 96%, rgba(110,140,124,.12) 0%, rgba(110,140,124,0) 40%),
  linear-gradient(148deg,#FDF7F6 0%,#FBF6F0 52%,#F7F3EC 100%);
  background-color:#FBF6F0;border:1px solid var(--line);align-items:center;
  min-height:252px}
.veil{position:absolute;inset:0;background:linear-gradient(102deg,
  rgba(43,31,28,.88) 0%,rgba(43,31,28,.66) 44%,rgba(43,31,28,.14) 100%)}
.inner{position:relative;padding:34px 38px 32px;width:100%;max-width:730px}
.brandrow{display:flex;align-items:center;gap:13px;margin-bottom:20px}
.brandrow img{height:32px;width:auto;display:block;padding:5px 9px;
  background:rgba(255,255,255,.95);border-radius:9px}
.brandrow .nm{font-size:11px;letter-spacing:.22em;color:rgba(255,255,255,.6);
  text-transform:uppercase;font-weight:500}
.cover.plain .brandrow .nm{color:#B4A9A0}
h1{font-size:31px;font-weight:600;letter-spacing:-.015em;margin:0 0 11px;
  line-height:1.3;color:#fff}
.cover.plain h1{color:#2E2825}
.lede{font-size:13.5px;color:rgba(255,255,255,.82);line-height:1.7;margin-bottom:18px}
.cover.plain .lede{color:#6F6862}
.cmeta{font-size:11.5px;color:rgba(255,255,255,.52);margin-top:3px;word-break:break-all}
.cover.plain .cmeta{color:#A79F97}
.cmeta code{background:rgba(255,255,255,.14);border-radius:5px;padding:1.5px 7px;
  font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:10.5px}
.cover.plain .cmeta code{background:rgba(60,40,35,.055)}

/* 提示条 */
.notice{display:flex;gap:11px;align-items:flex-start;background:var(--apri-bg);
  border:1px solid var(--apri-ln);color:var(--apri);border-radius:16px;
  padding:13px 17px;margin-bottom:30px;font-size:12.5px;line-height:1.65}
.notice .dot{flex:none;width:17px;height:17px;border-radius:50%;background:var(--apri);
  color:#fff;font-size:11px;line-height:17px;text-align:center;font-weight:600;margin-top:1px}
.notice b{font-weight:600}

/* 指标 */
.kpis{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:38px}
.kpi{background:var(--card);border:1px solid var(--line);border-radius:22px;
  padding:21px 23px 19px;box-shadow:var(--shadow)}
.kpi .v{font-size:35px;font-weight:600;letter-spacing:-.035em;line-height:1.08;
  font-variant-numeric:tabular-nums}
.kpi .v small{font-size:15px;font-weight:500;color:var(--ink2);margin-left:2px;
  letter-spacing:0}
.kpi .l{color:var(--ink2);font-size:11.5px;margin-top:6px;letter-spacing:.03em}
.kpi.hl{background:linear-gradient(152deg,#BC6C78 0%,#A4535F 100%);
  border-color:transparent;box-shadow:0 8px 24px rgba(178,96,108,.28)}
.kpi.hl .v{color:#fff}
.kpi.hl .l{color:rgba(255,255,255,.84)}

/* 区块 */
section{margin-bottom:38px}
h2{font-size:14.5px;font-weight:600;margin:0 0 15px;display:flex;align-items:center;
  gap:10px;letter-spacing:.01em}
h2 .mk{width:7px;height:7px;border-radius:50%;background:var(--rose);flex:none;
  box-shadow:0 0 0 3px var(--rose-bg)}
h2 em{font-style:normal;font-weight:400;font-size:12px;color:var(--ink3);letter-spacing:0}

/* 目录表 */
.dirlist{background:var(--card);border:1px solid var(--line);border-radius:20px;
  overflow:hidden;box-shadow:var(--shadow)}
.drow{display:grid;grid-template-columns:1.6fr 74px 74px 74px 96px 116px;
  align-items:center;gap:8px;padding:13px 22px;border-bottom:1px solid var(--line)}
.drow:last-child{border-bottom:none}
.drow.h{background:#FCFAF7;font-size:11.5px;color:var(--ink3);font-weight:500;
  letter-spacing:.03em;padding-top:11px;padding-bottom:11px}
.drow .nm{font-size:13px;font-weight:500;word-break:break-all}
.drow .num{text-align:right;font-variant-numeric:tabular-nums;font-size:13px;
  color:var(--ink2)}
.drow .num.zero{color:var(--ink3)}
.pill{display:inline-block;font-size:11px;padding:2px 9px;border-radius:20px;
  background:var(--apri-bg);color:var(--apri);border:1px solid var(--apri-ln);
  font-variant-numeric:tabular-nums;font-weight:500}
.pill.z{background:var(--sage-bg);color:var(--sage);border-color:var(--sage-ln)}
.bar{height:4px;border-radius:2px;background:#F1EBE4;overflow:hidden;margin-top:5px}
.bar i{display:block;height:100%;background:var(--apri-ln);border-radius:2px}

/* 分组卡 */
.group{background:var(--card);border:1px solid var(--line);border-radius:22px;
  padding:19px 21px 21px;margin-bottom:15px;box-shadow:var(--shadow)}
.ghead{display:flex;align-items:center;gap:11px;flex-wrap:wrap;margin-bottom:16px}
.gno{font-size:11.5px;font-weight:600;color:var(--rose-d);background:var(--rose-bg);
  border:1px solid var(--rose-ln);border-radius:9px;padding:2px 10px;
  font-variant-numeric:tabular-nums;letter-spacing:.02em}
.gtitle{font-weight:600;font-size:13.5px}
.tagx{font-size:11px;padding:2px 9px;border-radius:20px;font-weight:500}
.tagx.e{background:var(--sage-bg);color:var(--sage);border:1px solid var(--sage-ln)}
.tagx.s{background:var(--apri-bg);color:var(--apri);border:1px solid var(--apri-ln)}
.gmeta{color:var(--ink3);font-size:11.5px;margin-left:auto;display:flex;gap:16px;
  flex-wrap:wrap;font-variant-numeric:tabular-nums}

.items{display:grid;grid-template-columns:repeat(auto-fit,minmax(228px,318px));
  gap:15px;justify-content:start}
.item{border:1px solid var(--line);border-radius:18px;padding:11px;background:#FDFCFA;
  display:flex;flex-direction:column;transition:transform .16s ease,box-shadow .16s ease}
.item:hover{transform:translateY(-2px);box-shadow:0 6px 20px rgba(74,54,48,.08)}
.item.keep{background:var(--rose-bg);border-color:var(--rose-ln)}
.thumb{width:100%;height:186px;border-radius:12px;overflow:hidden;background:var(--sunk);
  display:flex;align-items:center;justify-content:center;margin-bottom:11px;cursor:zoom-in}
.thumb img{max-width:100%;max-height:186px;object-fit:contain;display:block;
  transition:opacity .16s}
.thumb:hover img{opacity:.86}
.flag{display:flex;align-items:center;gap:6px;font-size:11px;font-weight:600;
  margin-bottom:8px;letter-spacing:.02em}
.flag .d{width:6px;height:6px;border-radius:50%;flex:none}
.item.keep .flag{color:var(--rose-d)}
.item.keep .flag .d{background:var(--rose)}
.item .flag{color:var(--apri)}
.item .flag .d{background:var(--apri)}
.fname{font-size:12.5px;font-weight:500;word-break:break-all;margin-bottom:3px;
  line-height:1.45}
.fpath{font-size:11px;color:var(--ink3);word-break:break-all;line-height:1.5}
.fspec{font-size:11px;color:var(--ink2);margin-top:7px;
  font-variant-numeric:tabular-nums;letter-spacing:.01em}

.empty{background:var(--card);border:1px solid var(--line);border-radius:22px;
  padding:58px 30px;text-align:center;color:var(--ink2);font-size:13.5px;
  line-height:1.9;box-shadow:var(--shadow)}
.empty .big{font-size:15px;font-weight:600;color:var(--ink);margin-bottom:8px}

footer{margin-top:46px;padding-top:22px;border-top:1px solid var(--line);
  color:var(--ink3);font-size:11.5px;line-height:2}
footer code{font-family:ui-monospace,"SFMono-Regular",Consolas,monospace;font-size:11px;
  background:var(--sunk);border-radius:5px;padding:1.5px 6px;word-break:break-all;
  color:var(--ink2)}

#lb{display:none;position:fixed;inset:0;background:rgba(38,30,28,.93);z-index:999;
  align-items:center;justify-content:center;padding:44px;cursor:zoom-out}
#lb img{max-width:94vw;max-height:92vh;border-radius:10px;
  box-shadow:0 18px 70px rgba(0,0,0,.5)}

@media(max-width:880px){
  .kpis{grid-template-columns:repeat(2,1fr)}
  .drow{grid-template-columns:1fr 62px 62px;gap:6px;padding:12px 16px}
  .drow .hide-sm{display:none}
  .wrap{padding:20px 15px 60px}
  .inner{padding:24px 22px}
  h1{font-size:21px}
}
"""


ACTION_LABELS = {
    "report": ("重复项", "重复", "重复占用"),
    "isolate": ("移入隔离", "移出", "移出占用"),
    "collect": ("不复制", "跳过", "跳过占用"),
}

ACTION_NOTICE = {
    "report": "本次只生成报告，没有移动、复制或删除任何文件。",
    "isolate": "隔离动作只做「移动」，不删除任何文件；如需撤销，用同一程序点「还原隔离」。",
    "collect": "本次只「读取」源目录，未移动、未重命名、未删除任何文件；"
               "去重后的副本保存在新目录里。",
}


def write_html(
    result: ScanResult,
    out_dir: Path,
    decisions: dict | None = None,
    title: str = "图片排重报告",
    cover=None,
    logo=None,
    subtitle: str = "",
    action: str = "isolate",
) -> Path:
    from .isolator import plan_by_rule

    skip_flag, skip_word, skip_bytes_word = ACTION_LABELS.get(
        action, ACTION_LABELS["isolate"])
    notice_text = ACTION_NOTICE.get(action, ACTION_NOTICE["isolate"])

    plan = decisions if decisions is not None else plan_by_rule(result.groups, "best")
    esc = html.escape
    out: list[str] = []
    add = out.append

    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    has_similar = any(g.relation != EXACT for g in result.groups)
    cover_uri = _embed(cover, 1900, quality=74)
    logo_uri = _embed(logo, 200, keep_alpha=True)

    # 封面导语：把最重要的结论直接摆在第一屏
    if result.groups:
        lede = (f"共 {len(result.images)} 张图片，检出 {len(result.groups)} 组重复，"
                f"{result.extra_copies} 个多余副本可移出，占总体积 "
                f"{_fmt_size(result.reclaim_bytes)}。")
    else:
        lede = f"共 {len(result.images)} 张图片，未发现内容重复。"
    if subtitle:
        lede = f"{subtitle}<br>{lede}"

    brand = f'<img src="{logo_uri}" alt="">' if logo_uri else ""
    mode_txt = "SHA256 完全一致" + ("　+　感知哈希相似" if has_similar else "")

    if cover_uri:
        add(f"""<div class="cover" style="background-image:url('{cover_uri}')">
<div class="veil"></div><div class="inner">
<div class="brandrow">{brand}<span class="nm">duplicate image report</span></div>
<h1>{esc(title)}</h1>
<div class="lede">{lede}</div>
<div class="cmeta">扫描目录 <code>{esc(str(result.root))}</code></div>
<div class="cmeta">{now} · {esc(mode_txt)}</div>
</div></div>""")
    else:
        add(f"""<div class="cover plain"><div class="inner">
<div class="brandrow">{brand}<span class="nm">duplicate image report</span></div>
<h1>{esc(title)}</h1>
<div class="lede">{lede}</div>
<div class="cmeta">扫描目录 <code>{esc(str(result.root))}</code></div>
<div class="cmeta">{now} · {esc(mode_txt)}</div>
</div></div>""")

    add(f"""<div class="notice"><div class="dot">!</div><div>
<b>本报告含敏感文件的真实路径与缩略图，请勿外发。</b><br>
{esc(notice_text)}</div></div>""")

    add(f"""<div class="kpis">
<div class="kpi"><div class="v">{len(result.images)}</div><div class="l">扫描图片</div></div>
<div class="kpi"><div class="v">{len(result.groups)}</div><div class="l">重复组</div></div>
<div class="kpi hl"><div class="v">{result.extra_copies}</div><div class="l">可移出副本</div></div>
<div class="kpi"><div class="v">{_fmt_size(result.reclaim_bytes)}</div><div class="l">可释放空间</div></div>
</div>""")

    rows = _dir_summary(result, plan)
    if len(rows) > 1:
        add('<section><h2><span class="mk"></span>按目录汇总'
            '<em>移出之后每个目录还会剩多少</em></h2><div class="dirlist">')
        add(f'<div class="drow h"><div>目录</div><div class="num">图片</div>'
            f'<div class="num">保留</div><div class="num">{skip_word}</div>'
            f'<div class="num hide-sm">{skip_bytes_word}</div>'
            f'<div class="hide-sm">重复占比</div></div>')
        for r in rows:
            ratio = r["iso"] / r["total"] if r["total"] else 0
            cls = "pill z" if r["iso"] == 0 else "pill"
            name = "（根目录）" if r["dir"] == "." else r["dir"]
            add(f'<div class="drow"><div class="nm">{esc(name)}</div>'
                f'<div class="num">{r["total"]}</div>'
                f'<div class="num">{r["keep"]}</div>'
                f'<div class="num{" zero" if r["iso"] == 0 else ""}">{r["iso"]}</div>'
                f'<div class="num hide-sm">{_fmt_size(r["iso_bytes"])}</div>'
                f'<div class="hide-sm"><span class="{cls}">{ratio:.0%}</span>'
                f'<div class="bar"><i style="width:{ratio:.0%}"></i></div></div></div>')
        add("</div></section>")

    section_hint = {
        "report": "每组列出全部重复项，供人工核对",
        "isolate": "每组保留一份，其余移入隔离目录",
        "collect": "每组保留一份，其余不复制到新目录",
    }.get(action, "")
    add(f'<section><h2><span class="mk"></span>重复分组'
        f'<em>{esc(section_hint)}</em></h2>')

    if not result.groups:
        add('<div class="empty"><div class="big">没有发现重复图片</div>'
            '若确认存在「看起来一样但文件名不同」的图片，说明它们被重新保存过。<br>'
            '可把相似阈值从 6 调到 8~10 后重跑。</div>')
    else:
        for g in result.groups:
            if g.relation == EXACT:
                tag = '<span class="tagx e">字节相同</span>'
            else:
                tag = f'<span class="tagx s">相似 · 距离 {g.max_distance}</span>'
            add('<div class="group">')
            add(f'<div class="ghead"><span class="gno">{g.index:02d}</span>'
                f'<span class="gtitle">{len(g.members)} 份</span>{tag}'
                f'<span class="gmeta"><span>单份 {_fmt_size(g.members[0].size)}</span>'
                f'<span>可释放 {_fmt_size(g.reclaim_bytes)}</span></span></div>')
            add('<div class="items">')
            for seq, m in enumerate(g.members, 1):
                act = plan.get(str(m.path), "keep" if seq == 1 else "isolate")
                is_keep = act == "keep"
                res = f"{m.width}×{m.height}" if m.width else "—"
                add(f'<div class="item{" keep" if is_keep else ""}">'
                    f'<div class="flag"><span class="d"></span>'
                    f'{"保留" if is_keep else esc(skip_flag)}</div>'
                    f'<div class="thumb"><img src="{esc(_file_uri(m.path))}" alt="" loading="lazy"></div>'
                    f'<div class="fname">{esc(m.path.name)}</div>'
                    f'<div class="fpath">{esc(_rel_dir(m.path, result.root))}</div>'
                    f'<div class="fspec">{res} · {_fmt_size(m.size)} · '
                    f'{datetime.fromtimestamp(m.mtime):%m-%d %H:%M}</div></div>')
            add("</div></div>")
    add("</section>")

    if result.unreadable:
        add('<section><h2><span class="mk"></span>无法解码'
            '<em>未参与相似度比对</em></h2><div class="group"><div class="fpath">')
        for m in result.unreadable[:60]:
            add(f"{esc(str(m.path))} — {esc(m.error)}<br>")
        if len(result.unreadable) > 60:
            add(f"… 另有 {len(result.unreadable) - 60} 个")
        add("</div></div></section>")

    add(f"""<footer>
判定口径：<b>字节相同</b> = SHA256 一致，零误判；<b>相似</b> = pHash 与 dHash
汉明距离均在阈值内，且宽高比接近。<br>
清单文件 <code>{esc(str(out_dir / 'duplicates.csv'))}</code>　·　
扫描耗时 {result.elapsed:.1f} 秒　·　点击缩略图可放大
</footer></div>
<div id="lb"><img alt=""></div>
<script>
var lb=document.getElementById('lb'),lbi=lb.firstElementChild;
document.querySelectorAll('.thumb img').forEach(function(el){{
  el.addEventListener('click',function(e){{
    lbi.src=el.src;lb.style.display='flex';e.stopPropagation();
  }});
}});
lb.addEventListener('click',function(){{lb.style.display='none';lbi.src='';}});
document.addEventListener('keydown',function(e){{
  if(e.key==='Escape'){{lb.style.display='none';}}
}});
</script>
</body></html>""")

    head = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title>
<style>{CSS}</style></head><body><div class="wrap">
"""
    path = out_dir / "review.html"
    path.write_text(head + "\n".join(out), encoding="utf-8")
    return path
