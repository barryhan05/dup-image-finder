#Requires -Version 5.1
<#
.SYNOPSIS
    图片字节级排重（零依赖 / 只读 / 不改动任何原文件）

.DESCRIPTION
    两轮筛：先按文件大小分组（大小不同的绝不可能是副本），同大小的组内再算 SHA256。
    产出两份东西：
      1) duplicates.csv   可交给他人复核的清单
      2) review.html      并排缩略图，双击用浏览器打开即可肉眼确认

    能力边界：只认“字节完全一致”，即改过名的纯副本。
    被重新保存/压缩/缩放过的同一张图，本脚本抓不到，需要感知哈希（第二档）。

.PARAMETER Path
    待排重的图片目录。递归扫描子目录。

.PARAMETER ReportDir
    报告输出目录。默认在 Path 下建 _dup_report。

.PARAMETER SkipHash
    只按文件大小分组，不算哈希。极快，用于先摸清数据规模。

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Find-DupImages.ps1 -Path "D:\待排重"

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File .\Find-DupImages.ps1 -Path "D:\待排重" -ReportDir "D:\报告" -OpenReport
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,

    [string]$ReportDir = '',

    [switch]$SkipHash,

    [switch]$OpenReport
)

$ErrorActionPreference = 'Stop'

function Write-Step([string]$msg) {
    Write-Host $msg -ForegroundColor Cyan
}

function HtmlEnc([string]$s) {
    if ($null -eq $s) { return '' }
    return [System.Net.WebUtility]::HtmlEncode($s)
}

function FileUri([string]$p) {
    try { return ([System.Uri]$p).AbsoluteUri } catch { return '' }
}

function Fmt-Size([double]$bytes) {
    if ($bytes -ge 1GB) { return ('{0:N2} GB' -f ($bytes / 1GB)) }
    if ($bytes -ge 1MB) { return ('{0:N2} MB' -f ($bytes / 1MB)) }
    if ($bytes -ge 1KB) { return ('{0:N1} KB' -f ($bytes / 1KB)) }
    return ('{0} B' -f [int]$bytes)
}

# ---------- 0. 参数校验 ----------
if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
    throw "目录不存在: $Path"
}

$root = (Resolve-Path -LiteralPath $Path).ProviderPath
if ([string]::IsNullOrWhiteSpace($ReportDir)) {
    $ReportDir = Join-Path $root '_dup_report'
}
if (-not (Test-Path -LiteralPath $ReportDir)) {
    New-Item -ItemType Directory -Path $ReportDir -Force | Out-Null
}
$ReportDir = (Resolve-Path -LiteralPath $ReportDir).ProviderPath
$reportPrefix = $ReportDir.TrimEnd('\') + '\'

$sw = [System.Diagnostics.Stopwatch]::StartNew()

# ---------- 1. 扫描 ----------
Write-Step "[1/4] 扫描图片文件 ..."
$exts = @('.jpg', '.jpeg', '.png', '.bmp', '.gif', '.tif', '.tiff', '.webp', '.heic', '.heif', '.jfif', '.avif')

$files = @(
    Get-ChildItem -LiteralPath $root -Recurse -File -ErrorAction SilentlyContinue |
        Where-Object {
            $exts -contains $_.Extension.ToLowerInvariant() -and
            -not $_.FullName.StartsWith($reportPrefix, [System.StringComparison]::OrdinalIgnoreCase)
        }
)

Write-Host ("      命中 {0} 个图片文件，合计 {1}" -f $files.Count, (Fmt-Size (($files | Measure-Object -Property Length -Sum).Sum)))
if ($files.Count -eq 0) { throw "该目录下没有找到任何图片文件（支持的扩展名：$($exts -join ' ')）" }

# ---------- 2. 按大小预分组 ----------
Write-Step "[2/4] 按文件大小预分组 ..."
$sizeGroups = @($files | Group-Object -Property Length | Where-Object { $_.Count -gt 1 })
$candidateCount = 0
foreach ($g in $sizeGroups) { $candidateCount += $g.Count }
Write-Host ("      大小相同的候选 {0} 个；其余 {1} 个大小唯一，已直接排除" -f $candidateCount, ($files.Count - $candidateCount))

$cand = @(foreach ($g in $sizeGroups) { $g.Group })

# ---------- 3. 算哈希 + 分组 ----------
$hashInfo = @{}
if (-not $SkipHash -and $cand.Count -gt 0) {
    Write-Step "[3/4] 计算 SHA256 ..."
    $i = 0
    foreach ($f in $cand) {
        $i++
        if ($i % 200 -eq 0) { Write-Host ("      已处理 {0}/{1}" -f $i, $cand.Count) }
        $hashInfo[$f.FullName] = (Get-FileHash -LiteralPath $f.FullName -Algorithm SHA256).Hash
    }
}
else {
    Write-Step "[3/4] 跳过哈希（-SkipHash）"
    foreach ($f in $cand) { $hashInfo[$f.FullName] = ('SIZE-{0}' -f $f.Length) }
}

Write-Step "[4/4] 分组并生成报告 ..."
$dupRows = @()
$groupCount = 0
$reclaimBytes = 0

$grouped = @(
    $cand | Group-Object -Property { $hashInfo[$_.FullName] } | Where-Object { $_.Count -gt 1 }
)

foreach ($g in $grouped) {
    $groupCount++
    # 仅按修改时间排序展示，不构成“该保留哪个”的建议
    $items = @($g.Group | Sort-Object LastWriteTime, FullName)
    $seq = 0
    foreach ($it in $items) {
        $seq++
        $dupRows += [pscustomobject]@{
            Group      = $groupCount
            Seq        = $seq
            FileName   = $it.Name
            FullPath   = $it.FullName
            SizeBytes  = $it.Length
            SizeText   = (Fmt-Size $it.Length)
            Modified   = $it.LastWriteTime.ToString('yyyy-MM-dd HH:mm:ss')
            SHA256     = $hashInfo[$it.FullName]
            Note       = ''
        }
    }
    # 每组保留 1 份，其余视为“多余副本”
    $reclaimBytes += [double](($items | Select-Object -Skip 1 | Measure-Object -Property Length -Sum).Sum)
}

$extraCopies = $dupRows.Count - $groupCount

# ---------- CSV ----------
$csvPath = Join-Path $ReportDir 'duplicates.csv'
if ($dupRows.Count -gt 0) {
    $dupRows | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding UTF8
}
else {
    "Group,Seq,FileName,FullPath,SizeBytes,SizeText,Modified,SHA256,Note" |
        Set-Content -LiteralPath $csvPath -Encoding UTF8
}

# ---------- HTML ----------
$htmlPath = Join-Path $ReportDir 'review.html'
$now = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
$sb = New-Object System.Text.StringBuilder

[void]$sb.AppendLine('<!DOCTYPE html>')
[void]$sb.AppendLine('<html lang="zh-CN"><head><meta charset="utf-8">')
[void]$sb.AppendLine('<title>图片排重报告</title>')
[void]$sb.AppendLine(@'
<style>
  * { box-sizing: border-box; }
  body { margin: 0; padding: 28px 32px 60px; background: #f6f6f4; color: #2c2c2a;
         font-family: -apple-system, "Segoe UI", "Microsoft YaHei", sans-serif; font-size: 14px; line-height: 1.6; }
  h1 { font-size: 20px; font-weight: 500; margin: 0 0 4px; }
  .sub { color: #5f5e5a; font-size: 13px; margin-bottom: 20px; }
  .warn { background: #FAEEDA; border: 1px solid #EF9F27; color: #633806;
          padding: 10px 14px; border-radius: 8px; margin-bottom: 22px; font-size: 13px; }
  .stats { display: flex; gap: 12px; flex-wrap: wrap; margin-bottom: 26px; }
  .stat { background: #fff; border: 1px solid rgba(0,0,0,.12); border-radius: 10px; padding: 12px 18px; min-width: 130px; }
  .stat b { display: block; font-size: 22px; font-weight: 500; }
  .stat span { color: #5f5e5a; font-size: 12px; }
  .group { background: #fff; border: 1px solid rgba(0,0,0,.12); border-radius: 12px; padding: 16px 18px; margin-bottom: 16px; }
  .ghead { font-size: 14px; font-weight: 500; margin-bottom: 12px; }
  .ghead em { font-style: normal; color: #5f5e5a; font-weight: 400; font-size: 12px; margin-left: 8px; }
  .items { display: flex; gap: 14px; flex-wrap: wrap; }
  .item { width: 226px; border: 1px solid rgba(0,0,0,.1); border-radius: 8px; padding: 10px; background: #fbfbfa; }
  .item.first { border-color: #185FA5; background: #E6F1FB; }
  .thumb { width: 100%; height: 160px; display: flex; align-items: center; justify-content: center;
           background: #f1efe8; border-radius: 6px; overflow: hidden; margin-bottom: 8px; }
  .thumb img { max-width: 100%; max-height: 160px; object-fit: contain; }
  .fname { font-size: 13px; word-break: break-all; margin-bottom: 4px; }
  .meta { font-size: 11px; color: #5f5e5a; word-break: break-all; }
  .empty { background:#fff; border:1px solid rgba(0,0,0,.12); border-radius:12px; padding:36px; text-align:center; color:#5f5e5a; }
  .foot { color: #888780; font-size: 12px; margin-top: 30px; border-top: 1px solid rgba(0,0,0,.1); padding-top: 14px; }
</style></head><body>
'@)

[void]$sb.AppendLine(('<h1>图片排重报告 · 字节级</h1>'))
[void]$sb.AppendLine(('<div class="sub">扫描目录：{0} &nbsp;|&nbsp; 生成时间：{1} &nbsp;|&nbsp; 模式：{2}</div>' -f (HtmlEnc $root), $now, $(if ($SkipHash) { '仅按大小分组（未算哈希）' } else { 'SHA256 完全一致' })))
[void]$sb.AppendLine('<div class="warn"><b>本报告含敏感文件的真实路径与缩略图，请勿外发。</b> 报告本身是只读清单，脚本未移动、未重命名、未删除任何原文件。</div>')

[void]$sb.AppendLine('<div class="stats">')
[void]$sb.AppendLine(('<div class="stat"><b>{0}</b><span>扫描图片</span></div>' -f $files.Count))
[void]$sb.AppendLine(('<div class="stat"><b>{0}</b><span>重复组</span></div>' -f $groupCount))
[void]$sb.AppendLine(('<div class="stat"><b>{0}</b><span>多余副本</span></div>' -f $extraCopies))
[void]$sb.AppendLine(('<div class="stat"><b>{0}</b><span>理论可释放</span></div>' -f (Fmt-Size $reclaimBytes)))
[void]$sb.AppendLine('</div>')

if ($groupCount -eq 0) {
    [void]$sb.AppendLine('<div class="empty">没有发现字节级重复。<br>如果确认存在“看起来一样但文件名不同”的图片，说明它们被重新保存过，需要走第二档感知哈希。</div>')
}
else {
    $byGroup = $dupRows | Group-Object -Property Group
    foreach ($gb in $byGroup) {
        $rows = @($gb.Group)
        $head = $rows[0]
        [void]$sb.AppendLine('<div class="group">')
        [void]$sb.AppendLine(('<div class="ghead">第 {0} 组 · {1} 个副本 · 单份 {2}<em>顺序按修改时间排列，仅用于对照，不构成保留建议</em></div>' -f $head.Group, $rows.Count, (HtmlEnc $head.SizeText)))
        [void]$sb.AppendLine('<div class="items">')
        foreach ($r in $rows) {
            $cls = 'item'
            if ($r.Seq -eq 1) { $cls = 'item first' }
            $src = HtmlEnc (FileUri $r.FullPath)
            [void]$sb.AppendLine(('<div class="{0}">' -f $cls))
            [void]$sb.AppendLine(('<div class="thumb"><img src="{0}" alt="" loading="lazy"></div>' -f $src))
            [void]$sb.AppendLine(('<div class="fname">{0}</div>' -f (HtmlEnc $r.FileName)))
            [void]$sb.AppendLine(('<div class="meta">{0}<br>{1}</div>' -f (HtmlEnc $r.Modified), (HtmlEnc $r.FullPath)))
            [void]$sb.AppendLine('</div>')
        }
        [void]$sb.AppendLine('</div></div>')
    }
}

[void]$sb.AppendLine(('<div class="foot">能力边界：本报告只识别「字节完全一致」的副本。被重新压缩、转换格式、缩放或加过水印的同一张图不会被列出。<br>清单文件：{0}</div>' -f (HtmlEnc $csvPath)))
[void]$sb.AppendLine('</body></html>')

[System.IO.File]::WriteAllText($htmlPath, $sb.ToString(), (New-Object System.Text.UTF8Encoding($true)))

$sw.Stop()

# ---------- 控制台摘要 ----------
$lines = @(
    '',
    '================= 排重结果 =================',
    ('  扫描图片      : {0} 个' -f $files.Count),
    ('  大小相同候选  : {0} 个' -f $candidateCount),
    ('  重复组        : {0} 组' -f $groupCount),
    ('  多余副本      : {0} 个' -f $extraCopies),
    ('  理论可释放    : {0}' -f (Fmt-Size $reclaimBytes)),
    ('  耗时          : {0:N1} 秒' -f $sw.Elapsed.TotalSeconds),
    '--------------------------------------------',
    ("  清单 : $csvPath"),
    ("  对照 : $htmlPath"),
    '============================================'
)
$lines | ForEach-Object { Write-Host $_ }

if ($OpenReport -and (Test-Path -LiteralPath $htmlPath)) {
    Start-Process $htmlPath
}
