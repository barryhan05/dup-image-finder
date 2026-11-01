"""本地图片排重 · 单文件工具

三种处理方式：
  report   只出报告，什么都不动
  isolate  把多余副本移到隔离目录（可还原）
  collect  复制一份去重后的全集到新目录（源目录完全不动）

用法：
  双击          → 图形界面
  带参数运行    → 命令行
"""

from __future__ import annotations

import argparse
import sys
import threading
import traceback
from pathlib import Path

from app import copier, isolator, reporter, scanner

APP_NAME = "图片排重"
APP_VER = "1.1"

ACTIONS = {
    "collect": ("复制精简", "原目录完全不动，另存一份去重副本"),
    "report": ("只出报告", "原目录不会被修改"),
    "isolate": ("移动隔离", "原目录会减少，可一键还原"),
}

SKIP_LABELS = {"report": "重复项", "isolate": "移入隔离", "collect": "不复制"}


# --------------------------------------------------------------------------
# 命令行
# --------------------------------------------------------------------------
def run_cli(args) -> int:
    if args.restore:
        res = isolator.restore(
            Path(args.restore), dry_run=args.dry_run,
            progress=lambda m: print("  " + m, flush=True))
        print()
        print(f"还原完成：{res['restored']} 个文件")
        for src, why in res["skipped"][:20]:
            print(f"  跳过 {src} — {why}")
        for src, why in res["failed"][:20]:
            print(f"  失败 {src} — {why}")
        return 0

    if not args.path:
        print("缺少 --path 参数。直接双击运行可打开图形界面。", file=sys.stderr)
        return 2

    root = Path(args.path)
    if not root.is_dir():
        print(f"目录不存在：{root}", file=sys.stderr)
        return 2

    if args.isolate and args.collect:
        print("--isolate 与 --collect 不能同时使用：一个会动原目录，一个保证不动。",
              file=sys.stderr)
        return 2

    action = "isolate" if args.isolate else ("collect" if args.collect else "report")

    out_dir = Path(args.out) if args.out else root / "_dup_report"

    # 复制模式下报告不能落进源目录，否则等于往原目录里写东西
    if action == "collect":
        src_res = root.resolve()
        out_res = out_dir.resolve()
        if out_res == src_res or src_res in out_res.parents:
            out_dir = Path(args.collect_to or copier.default_collection_root(root)).parent / "_dup_report"
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        out_dir = root / "_dup_report"
        out_dir.mkdir(parents=True, exist_ok=True)

    target = None
    if action == "isolate":
        target = (Path(args.isolated_root) if args.isolated_root
                  else isolator.default_isolated_root(root))
    elif action == "collect":
        target = (Path(args.collect_to) if args.collect_to
                  else copier.default_collection_root(root))

    print(f"[1/3] 扫描 {root}")
    result = scanner.scan(root, threshold=args.threshold, mode=args.mode,
                          skip_dirs=[str(target)] if target else [],
                          progress=lambda m: print("      " + m, flush=True))

    print("[2/3] 生成报告")
    plan = isolator.plan_by_rule(result.groups, args.keep)
    action_of = {str(k): ("keep" if v == "keep" else "skip") for k, v in plan.items()}
    csv_path = reporter.write_csv(result, out_dir, action_of,
                                  skip_label=SKIP_LABELS[action])
    html_path = reporter.write_html(
        result, out_dir, action_of, title=args.title, cover=args.cover,
        logo=args.logo, subtitle=args.subtitle, action=action)

    print(f"[3/3] {ACTIONS[action][0]}")
    if action == "isolate" and result.groups:
        manifest = isolator.execute(
            result.groups, plan, target, rule=args.keep, dry_run=args.dry_run,
            scan_root=root, progress=lambda m: print("      " + m, flush=True))
        verb = "将移动" if args.dry_run else "已移动"
        print(f"      {verb} {manifest['moved_count']} 个文件 → {target}")
    elif action == "collect" and result.groups:
        manifest = copier.collect(
            result, target, rule=args.keep, keep_structure=not args.flatten,
            verify_hash=args.verify_hash,
            progress=lambda m: print("      " + m, flush=True))
        print(f"      已复制 {manifest['copied_count']} 个文件 → {target}")
        print(f"      跳过重复 {manifest['skipped_duplicates']} 个")
        print(f"      源目录未改动（只读取，未移动未删除）")
    else:
        print("      未改动任何文件")

    print()
    print("=" * 52)
    print(f"  扫描图片   {len(result.images)}")
    print(f"  重复组     {len(result.groups)}")
    print(f"  多余副本   {result.extra_copies}")
    print(f"  可释放     {reporter._fmt_size(result.reclaim_bytes)}")
    print(f"  耗时       {result.elapsed:.1f} 秒")
    print("-" * 52)
    print(f"  清单 {csv_path}")
    print(f"  报告 {html_path}")
    if action != "report" and result.groups:
        print(f"  结果 {target}")
    print("=" * 52)

    if args.open:
        import os

        os.startfile(html_path)  # noqa: S606
    return 0


# --------------------------------------------------------------------------
# 图形界面
# --------------------------------------------------------------------------
def run_gui():
    import queue
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    try:
        from ctypes import windll

        windll.shcore.SetProcessDpiAwareness(1)
    except Exception:  # noqa: BLE001
        pass

    BG = "#F5F0E8"
    CARD = "#FFFFFF"
    SUNK = "#FDFBF8"
    INK = "#322D2B"
    INK2 = "#7C7570"
    INK3 = "#ADA69E"
    LINE = "#E8E0D6"
    ROSE = "#B2606C"
    ROSE_D = "#9C5560"

    root = tk.Tk()
    root.title(f"{APP_NAME} v{APP_VER}")
    root.geometry("830x830")
    root.minsize(790, 660)
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass

    F_UI = "Microsoft YaHei UI"
    style.configure("TFrame", background=BG)
    style.configure("TCheckbutton", background=CARD, foreground=INK,
                    font=(F_UI, 9), focuscolor=CARD)
    style.map("TCheckbutton", background=[("active", CARD)])
    style.configure("TRadiobutton", background=CARD, foreground=INK,
                    font=(F_UI, 9), focuscolor=CARD)
    style.map("TRadiobutton", background=[("active", CARD)])
    style.configure("Ghost.TButton", font=(F_UI, 9), padding=(13, 6),
                    background=CARD, foreground=INK2, borderwidth=1, focuscolor=CARD)
    style.map("Ghost.TButton",
              background=[("active", "#F7F2EC"), ("disabled", "#FAF7F3")],
              foreground=[("disabled", "#C9C2BA")])
    style.configure("TCombobox", fieldbackground=SUNK, background=SUNK,
                    foreground=INK, arrowcolor=INK2, borderwidth=1, padding=(6, 4))
    style.configure("Horizontal.TProgressbar", background=ROSE,
                    troughcolor="#F1EAE3", borderwidth=0, thickness=7)

    class PillButton(tk.Canvas):
        def __init__(self, parent, text, command, bg=ROSE, fg="#FFFFFF",
                     hover=ROSE_D, width=132, height=42, radius=21,
                     font=(F_UI, 10, "bold")):
            super().__init__(parent, width=width, height=height, bg=BG,
                             highlightthickness=0, cursor="hand2")
            self._bg, self._hover, self._cmd = bg, hover, command
            self._enabled = True
            self._r = radius
            self._cw, self._ch = width, height  # 不能用 _w/_h，tkinter 内部占用
            self._shape = self._draw(bg)
            self._text_id = self.create_text(width / 2, height / 2 + 1, text=text,
                                             fill=fg, font=font)
            self.bind("<Button-1>", self._click)
            self.bind("<Enter>", self._enter)
            self.bind("<Leave>", self._leave)

        def _draw(self, fill):
            x1, y1, x2, y2, r = 1, 1, self._cw - 1, self._ch - 1, self._r
            pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r,
                   x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
                   x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
            return self.create_polygon(pts, smooth=True, fill=fill, outline="")

        def _click(self, _):
            if self._enabled and self._cmd:
                self._cmd()

        def _enter(self, _):
            if self._enabled:
                self.itemconfig(self._shape, fill=self._hover)

        def _leave(self, _):
            self.itemconfig(self._shape, fill=self._bg)

        def set_enabled(self, on):
            self._enabled = on
            self.configure(cursor="hand2" if on else "arrow")
            self.itemconfig(self._shape, fill=self._bg if on else "#EAD9DC")

    msg_q: queue.Queue = queue.Queue()
    state = {"busy": False, "html": None, "target": None}

    # ---------------- 标题 ----------------
    head = tk.Frame(root, bg=BG)
    head.pack(fill="x", padx=28, pady=(20, 12))
    trow = tk.Frame(head, bg=BG)
    trow.pack(fill="x")
    tk.Label(trow, text="图片排重", bg=BG, fg=INK,
             font=(F_UI, 17, "bold")).pack(side="left")
    tk.Label(trow, text=f"v{APP_VER}", bg=BG, fg=INK3,
             font=(F_UI, 8)).pack(side="left", padx=(7, 0), pady=(6, 0))
    tk.Label(head, text="全程本地运行　·　不上传任何数据　·　处理方式由你决定",
             bg=BG, fg=INK2, font=(F_UI, 9)).pack(anchor="w", pady=(3, 0))

    def card():
        f = tk.Frame(root, bg=CARD, highlightbackground=LINE, highlightthickness=1)
        f.pack(fill="x", padx=28, pady=(0, 11))
        inner = tk.Frame(f, bg=CARD)
        inner.pack(fill="x", padx=20, pady=16)
        return inner

    # ---------------- 路径 ----------------
    pathcard = card()
    vars_ = {"path": tk.StringVar(), "out": tk.StringVar(),
             "target": tk.StringVar()}

    action_var = tk.StringVar(value="collect")

    def target_default(p):
        if action_var.get() == "isolate":
            return str(isolator.default_isolated_root(Path(p)))
        return str(copier.default_collection_root(Path(p)))

    target_row = {}

    def path_row(parent, label, key, hint, is_dir=True, first=False):
        outer = tk.Frame(parent, bg=CARD)
        outer.pack(fill="x", pady=(0 if first else 8, 0))
        f = tk.Frame(outer, bg=CARD)
        f.pack(fill="x")
        lbl = tk.Label(f, text=label, bg=CARD, fg=INK2, font=(F_UI, 9),
                       width=10, anchor="w")
        lbl.pack(side="left")
        e = tk.Entry(f, textvariable=vars_[key], bg=SUNK, fg=INK, relief="flat",
                     font=(F_UI, 9), highlightthickness=1,
                     highlightbackground=LINE, highlightcolor=ROSE)
        e.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 8))

        def browse():
            if is_dir:
                p = filedialog.askdirectory(title=label)
            else:
                p = filedialog.askopenfilename(
                    title=label,
                    filetypes=[("图片", "*.jpg *.jpeg *.png *.bmp *.webp"),
                               ("所有文件", "*.*")])
            if p:
                vars_[key].set(p)
                if key == "path":
                    vars_["out"].set(str(Path(p) / "_dup_report"))
                    vars_["target"].set(target_default(p))

        ttk.Button(f, text="浏览", style="Ghost.TButton", command=browse,
                   width=5).pack(side="left")
        hint_lbl = tk.Label(outer, text=hint, bg=CARD, fg=INK3, font=(F_UI, 8))
        hint_lbl.pack(anchor="w", padx=(80, 0), pady=(2, 0))
        target_row[key] = (outer, lbl, hint_lbl, e)
        return e

    path_row(pathcard, "扫描目录", "path",
             "支持子目录递归 · jpg/png/webp/heic 等", first=True)
    path_row(pathcard, "报告输出", "out", "默认放在扫描目录下的 _dup_report")
    path_row(pathcard, "精简版目录", "target", "新建的文件夹，原目录保持不动")

    # ---------------- 选项 ----------------
    optcard = card()

    line1 = tk.Frame(optcard, bg=CARD)
    line1.pack(fill="x")
    tk.Label(line1, text="处理方式", bg=CARD, fg=INK2, font=(F_UI, 9),
             width=10, anchor="w").pack(side="left")
    for key in ("collect", "report", "isolate"):
        ttk.Radiobutton(line1, text=ACTIONS[key][0], variable=action_var,
                        value=key).pack(side="left", padx=(0, 13))

    act_hint = tk.Label(optcard, text="", bg=CARD, fg=ROSE, font=(F_UI, 8))
    act_hint.pack(anchor="w", padx=(80, 0), pady=(3, 0))

    line2 = tk.Frame(optcard, bg=CARD)
    line2.pack(fill="x", pady=(9, 0))
    tk.Label(line2, text="判定方式", bg=CARD, fg=INK2, font=(F_UI, 9),
             width=10, anchor="w").pack(side="left")
    mode_var = tk.StringVar(value="both")
    ttk.Radiobutton(line2, text="精确 + 相似", variable=mode_var,
                    value="both").pack(side="left")
    ttk.Radiobutton(line2, text="仅精确（字节相同）", variable=mode_var,
                    value="exact").pack(side="left", padx=(14, 0))

    line3 = tk.Frame(optcard, bg=CARD)
    line3.pack(fill="x", pady=(9, 0))
    tk.Label(line3, text="相似阈值", bg=CARD, fg=INK2, font=(F_UI, 9),
             width=10, anchor="w").pack(side="left")
    thr_var = tk.IntVar(value=6)
    thr_scale = ttk.Scale(line3, from_=0, to=16, variable=thr_var,
                          orient="horizontal", length=250)
    thr_scale.pack(side="left")
    thr_lbl = tk.Label(line3, text="6", bg=CARD, fg=ROSE,
                       font=(F_UI, 10, "bold"), width=3)
    thr_lbl.pack(side="left", padx=(10, 0))
    tk.Label(line3, text="越大越宽松", bg=CARD, fg=INK3,
             font=(F_UI, 8)).pack(side="left", padx=(4, 0))

    def on_thr(*_):
        v = int(round(float(thr_var.get())))
        thr_var.set(v)
        thr_lbl.configure(text=str(v))

    thr_scale.configure(command=on_thr)

    line4 = tk.Frame(optcard, bg=CARD)
    line4.pack(fill="x", pady=(9, 0))
    tk.Label(line4, text="保留规则", bg=CARD, fg=INK2, font=(F_UI, 9),
             width=10, anchor="w").pack(side="left")
    keep_var = tk.StringVar(value="best")
    keep_box = ttk.Combobox(
        line4, textvariable=keep_var, state="readonly", width=44, font=(F_UI, 9),
        values=[f"{k} — {v}" for k, v in isolator.KEEP_RULES.items()])
    keep_box.current(0)
    keep_box.pack(side="left")

    line5 = tk.Frame(optcard, bg=CARD)
    line5.pack(fill="x", pady=(10, 0))
    tk.Label(line5, text="复制选项", bg=CARD, fg=INK2, font=(F_UI, 9),
             width=10, anchor="w").pack(side="left")
    verify_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(line5, variable=verify_var,
                    text="校验 SHA256").pack(side="left", padx=(0, 16))
    flat_var = tk.BooleanVar(value=False)
    ttk.Checkbutton(line5, variable=flat_var,
                    text="全部放同一层").pack(side="left")
    tk.Label(line5, text="（默认保留原子目录结构，避免同名冲突）", bg=CARD, fg=INK3,
             font=(F_UI, 8)).pack(side="left", padx=(6, 0))

    # -------- 底部（先占底边，避免被日志区挤出可视范围）--------
    foot = tk.Frame(root, bg=BG)
    foot.pack(side="bottom", fill="x", padx=28, pady=(11, 15))

    # ---------------- 操作 ----------------
    act = tk.Frame(root, bg=BG)
    act.pack(fill="x", padx=28, pady=(2, 9))
    go_btn = PillButton(act, "开始处理", None)
    go_btn.pack(side="left")
    prog = ttk.Progressbar(act, mode="determinate", style="Horizontal.TProgressbar")
    prog.pack(side="left", fill="x", expand=True, padx=(16, 0))

    # ---------------- 日志 ----------------
    log_wrap = tk.Frame(root, bg=BG)
    log_wrap.pack(fill="both", expand=True, padx=28)
    log = tk.Text(log_wrap, height=7, wrap="word", font=("Consolas", 9),
                  bg=SUNK, fg=INK, relief="flat", highlightthickness=1,
                  highlightbackground=LINE, padx=12, pady=9, insertbackground=INK)
    log.pack(fill="both", expand=True)
    log.configure(state="disabled")
    log.tag_configure("ok", foreground="#5C7F6B")
    log.tag_configure("warn", foreground="#B9824F")

    open_report = ttk.Button(foot, text="打开报告", style="Ghost.TButton",
                             state="disabled")
    open_report.pack(side="left")
    open_target = ttk.Button(foot, text="打开结果目录", style="Ghost.TButton",
                             state="disabled")
    open_target.pack(side="left", padx=(9, 0))
    restore_btn = ttk.Button(foot, text="还原隔离…", style="Ghost.TButton")
    restore_btn.pack(side="right")

    def logline(text, tag=None):
        log.configure(state="normal")
        log.insert("end", text + "\n", tag or ())
        log.see("end")
        log.configure(state="disabled")

    def on_action():
        a = action_var.get()
        act_hint.configure(text=ACTIONS[a][1])
        outer, lbl, hint_lbl, _ = target_row["target"]
        if a == "report":
            outer.pack_forget()
            return
        if a == "isolate":
            lbl.configure(text="隔离目录")
            hint_lbl.configure(text="重复项会被移动到此处，可一键还原")
        else:
            lbl.configure(text="精简版目录")
            hint_lbl.configure(text="新建的文件夹，原目录保持不动")
        # 重新插到"报告输出"下面
        outer.pack(fill="x", pady=(8, 0), after=target_row["out"][0])

    def poll():
        try:
            while True:
                kind, payload = msg_q.get_nowait()
                if kind == "log":
                    logline(payload)
                elif kind == "ok":
                    logline(payload, "ok")
                elif kind == "indeterminate":
                    prog.configure(mode="indeterminate")
                    prog.start(14)
                elif kind == "done":
                    prog.stop()
                    prog.configure(mode="determinate", value=100)
                    finish(payload)
                elif kind == "error":
                    prog.stop()
                    prog.configure(mode="determinate", value=0)
                    set_busy(False)
                    logline("出错：" + payload, "warn")
                    messagebox.showerror(APP_NAME, payload)
        except queue.Empty:
            pass
        root.after(90, poll)

    def set_busy(busy):
        state["busy"] = busy
        go_btn.set_enabled(not busy)
        restore_btn.configure(state="disabled" if busy else "normal")

    def finish(payload):
        set_busy(False)
        state["html"] = payload.get("html")
        state["target"] = payload.get("target")
        open_report.configure(state="normal" if state["html"] else "disabled")
        open_target.configure(state="normal" if state["target"] else "disabled")
        r = payload["result"]
        logline("")
        logline(f"完成　扫描 {len(r.images)} 张 · 重复 {len(r.groups)} 组 · "
                f"多余副本 {r.extra_copies} 个 · 可释放 "
                f"{reporter._fmt_size(r.reclaim_bytes)} · 耗时 {r.elapsed:.1f} 秒", "ok")

    def worker(path, out, target, action, mode, threshold, keep, verify, flat):
        try:
            msg_q.put(("log", f"扫描目录　{path}"))
            msg_q.put(("indeterminate", None))

            def progress(m):
                msg_q.put(("log", "　" + m))

            result = scanner.scan(Path(path), threshold=threshold, mode=mode,
                                  skip_dirs=[target] if (action != "report" and target) else [],
                                  progress=progress)

            msg_q.put(("log", "生成报告…"))
            plan = isolator.plan_by_rule(result.groups, keep)
            action_of = {str(k): ("keep" if v == "keep" else "skip")
                         for k, v in plan.items()}
            reporter.write_csv(result, Path(out), action_of,
                               skip_label=SKIP_LABELS.get(action, "移入隔离"))
            html_path = reporter.write_html(result, Path(out), action_of,
                                            action=action)

            used = None
            if action == "isolate" and result.groups:
                msg_q.put(("log", f"移动到隔离目录　{target}"))
                m = isolator.execute(result.groups, plan, Path(target),
                                     rule=keep, scan_root=path, progress=progress)
                used = m["isolated_root"]
                msg_q.put(("log", f"已移动 {m['moved_count']} 个文件（可还原）"))
            elif action == "collect" and result.groups:
                msg_q.put(("log", f"复制到精简目录　{target}"))
                m = copier.collect(result, Path(target), rule=keep,
                                   keep_structure=not flat,
                                   verify_hash=verify, progress=progress)
                used = m["collection_root"]
                msg_q.put(("ok", f"已复制 {m['copied_count']} 个文件，"
                                 f"跳过重复 {m['skipped_duplicates']} 个"))
                msg_q.put(("ok", "源目录未改动（只读取，未移动未删除）"))

            msg_q.put(("done", {"result": result, "html": str(html_path),
                                "target": used}))
        except Exception:  # noqa: BLE001
            msg_q.put(("error", traceback.format_exc(limit=2)))

    def start():
        if state["busy"]:
            return
        path = vars_["path"].get().strip()
        if not path or not Path(path).is_dir():
            messagebox.showwarning(APP_NAME, "请先选择一个存在的扫描目录。")
            return

        action = action_var.get()
        out = vars_["out"].get().strip() or str(Path(path) / "_dup_report")
        target = vars_["target"].get().strip()
        keep = keep_box.get().split(" — ")[0].strip()

        if action != "report":
            if not target:
                target = target_default(path)
                vars_["target"].set(target)
            try:
                copier._check_not_inside(Path(target), Path(path))
            except ValueError as exc:
                messagebox.showwarning(APP_NAME, str(exc))
                return

            if action == "collect":
                ok = messagebox.askyesno(
                    "确认复制",
                    f"将把去重后的图片复制到：\n{target}\n\n"
                    f"· 源目录完全不动，只读取\n"
                    f"· 每组保留一份，其余不复制\n"
                    f"· 文件夹结构保持不变\n\n是否继续？")
            else:
                ok = messagebox.askyesno(
                    "确认隔离",
                    f"重复项会被【移动】到：\n{target}\n\n"
                    f"· 每组至少保留一份在原位置\n"
                    f"· 不会删除任何文件，可随时还原\n\n是否继续？")
            if not ok:
                return

        try:
            Path(out).mkdir(parents=True, exist_ok=True)
        except OSError:
            out = str(Path(path) / "_dup_report")
            Path(out).mkdir(parents=True, exist_ok=True)

        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        set_busy(True)
        threading.Thread(target=worker, args=(
            path, out, target, action, mode_var.get(), int(thr_var.get()), keep,
            verify_var.get(), flat_var.get()), daemon=True).start()

    def do_restore():
        p = filedialog.askopenfilename(
            title="选择隔离目录里的 manifest.json",
            filetypes=[("还原清单", "manifest.json"), ("JSON", "*.json"),
                       ("所有文件", "*.*")])
        if not p:
            return
        if not messagebox.askyesno(
                "确认还原",
                "将按清单把文件移回原位置。\n原位置已有同名文件时会自动改名，不会覆盖。\n\n继续？"):
            return
        try:
            res = isolator.restore(Path(p))
            msg = f"已还原 {res['restored']} 个文件"
            if res["skipped"]:
                msg += f"\n跳过 {len(res['skipped'])} 个"
            if res["failed"]:
                msg += f"\n失败 {len(res['failed'])} 个"
            messagebox.showinfo(APP_NAME, msg)
            logline(msg.replace("\n", "　·　"), "ok")
        except Exception as exc:  # noqa: BLE001
            messagebox.showerror(APP_NAME, str(exc))

    def open_path(p):
        if p and Path(p).exists():
            import os

            os.startfile(p)  # noqa: S606

    go_btn._cmd = start
    restore_btn.configure(command=do_restore)
    open_report.configure(command=lambda: open_path(state["html"]))
    open_target.configure(command=lambda: open_path(state["target"]))

    for key in ("collect", "report", "isolate"):
        pass
    for w in line1.winfo_children():
        if isinstance(w, ttk.Radiobutton):
            w.configure(command=on_action)

    on_action()
    root.after(90, poll)
    root.mainloop()


# --------------------------------------------------------------------------
def main() -> int:
    parser = argparse.ArgumentParser(
        prog="DupScan",
        description="图片排重·精简版：只出报告 / 移动隔离 / 复制精简（源目录完全不动）")
    parser.add_argument("-p", "--path", help="扫描目录（递归）")
    parser.add_argument("-o", "--out", help="报告输出目录，默认 <path>/_dup_report")
    parser.add_argument("--mode", choices=["both", "exact"], default="both",
                        help="both=精确+相似（默认）；exact=仅字节相同")
    parser.add_argument("--threshold", type=int, default=6,
                        help="相似阈值 0-16，越大越宽松，默认 6")
    parser.add_argument("--keep", choices=list(isolator.KEEP_RULES), default="best",
                        help="每组保留哪一份，默认 best")
    parser.add_argument("--isolate", action="store_true",
                        help="把多余副本移动到隔离目录（会改动源目录）")
    parser.add_argument("--isolated-root", help="隔离目录路径")
    parser.add_argument("--collect", action="store_true",
                        help="复制一份去重后的副本到新目录（源目录完全不动）")
    parser.add_argument("--collect-to", help="精简版输出目录")
    parser.add_argument("--flatten", action="store_true",
                        help="精简版不保持目录层级，全部平铺")
    parser.add_argument("--verify-hash", action="store_true",
                        help="复制后逐个校验 SHA256")
    parser.add_argument("--dry-run", action="store_true", help="只演算不实际移动")
    parser.add_argument("--restore", help="按 manifest.json 还原隔离")
    parser.add_argument("--cover", help="报告封面图")
    parser.add_argument("--logo", help="报告 Logo")
    parser.add_argument("--title", default="图片排重报告", help="报告标题")
    parser.add_argument("--subtitle", default="", help="报告副标题")
    parser.add_argument("--open", action="store_true", help="完成后打开报告")

    args = parser.parse_args()

    if len(sys.argv) == 1:
        try:
            run_gui()
            return 0
        except Exception:  # noqa: BLE001
            traceback.print_exc()
            return 1

    return run_cli(args)


if __name__ == "__main__":
    sys.exit(main())
