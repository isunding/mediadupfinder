# mediadupfinder

按元数据分组的媒体文件查重工具。扫描文件夹或盘符，基于 **大小、时长、分辨率、码率、文件名** 等特征自动识别重复/近似文件对，输出结构化报告，按可释放空间降序排列，优先清理高价值目标。

## 功能概览

- **三级候选分组**：强 / 中 / 弱，覆盖从"几乎确定重复"到"可能是同一内容的不同版本"
- **全局去重**：三档之间共享已认领的文件对，不交叉重复报告
- **传递闭包聚类**：同大小文件按时长做并查集合并，避免链式相近被拆开
- **浪费空间估算**：按 `(文件数 - 1) × 中位大小` 估算每组可释放空间，全量按此降序
- **多格式输出**：JSON（默认）+ CSV + HTML，`--dry-run` 只打印不写文件
- **多排除关键字**：`--exclude-dir` 可多次指定，空串显式关闭
- **可控并发**：`--workers N` 指定线程池大小，`tqdm` 进度条（若已安装）
- **失败溯源**：读取失败记录原因（`parse_error / no_tracks / missing_file_size / missing_duration`），结束时按原因聚合计数
- **跨平台**：Windows 盘符扫描（`--drives G-U`），macOS / Linux 文件夹模式

---

## 目录

- [安装](#安装)
- [快速开始](#快速开始)
- [分组规则](#分组规则)
  - [强候选](#强候选strong)
  - [中候选](#中候选mid)
  - [弱候选](#弱候选weak)
- [推荐保留策略](#推荐保留策略)
- [完整参数](#完整参数)
- [输出格式](#输出格式)
  - [JSON](#json)
  - [CSV](#csv)
  - [HTML](#html)
- [性能与库模式](#性能与库模式)
- [常见问题](#常见问题)

---

## 安装

依赖 `pymediainfo` 和底层 `libmediainfo` / `mediainfo`：

```bash
pip install pymediainfo
# 可选，提供漂亮的进度条
pip install tqdm
```

底层 MediaInfo 库：

| 平台 | 安装方式 |
|------|---------|
| Windows | 下载 DLL 放进 `PATH`：https://mediaarea.net/en/MediaInfo/Download/Windows |
| macOS | `brew install mediainfo` |
| Linux | `sudo apt install mediainfo` 以及 `sudo apt install libmediainfo0v5`（库模式） |

> 若系统只装了 CLI 而没装库，`pymediainfo` 会退化为每个文件起一个子进程，线程池收益会明显下降。建议安装库版本以获得最佳性能。

脚本本身只有一个文件，直接下载 `mediadupfinder.py` 即可。

---

## 快速开始

扫描单个文件夹：

```bash
python mediadupfinder.py D:\Videos
```

扫描多块硬盘（Windows），按盘符范围：

```bash
python mediadupfinder.py --drives G-U
```

带过滤条件 + 多格式输出：

```bash
python mediadupfinder.py --drives G-U ^
    --min-size-mb 50 ^
    --duration-tol 2.0 ^
    --name-sim 0.85 ^
    --exclude-dir CHN --exclude-dir TMP ^
    --workers 24 ^
    -o result.json --csv result.csv --html report.html
```

只预览不写文件：

```bash
python mediadupfinder.py /some/folder --dry-run
```

---

## 分组规则

扫描阶段为每个文件提取元数据（路径、大小、时长、分辨率、视频码率、音频码率、格式）。分组阶段按以下三档从强到弱依次匹配，**同一对文件只出现在最先命中的一档里**。

### 强候选（strong）

> **大小完全相同 + 时长误差 ≤ `--duration-tol`（默认 1.0s）**

按 `size` 分桶，桶内按时长排序后做 **并查集合并**：任意两文件时长差 ≤ tol 就 union，最终同一连通分量里的所有文件进同一组。这保证了传递闭包 —— 比如 `[0, 0.9, 1.8]`、tol=1.0 的三个文件会被正确合并。

最可靠的一档，命中通常意味着同一文件的精确拷贝或不同容器封装。

### 中候选（mid）

两个子情况：

| 子情况 | 条件 |
|--------|------|
| A | 文件名相似（≥ `--name-sim`，默认 0.8）**+** 分辨率和视频码率相同 |
| B | 文件名归一化后相同 **+** 分辨率不同 **或** 码率不同 |

子情况 A 的相似度判定先跑安全剪枝：若 `|len_a - len_b| > (1 - threshold) × (len_a + len_b) + 1`，直接返回 0，避免无意义的 `SequenceMatcher`。同时内部会跳过已被强候选覆盖的对（同 size 且时长差 ≤ strong_tol）。

### 弱候选（weak）

> **时长差 ≤ `--weak-duration-tol`（默认 2.0s）+ 分辨率相同 + 大小不同**

按分辨率分桶后按时长滑动窗口比较。**重要修正**：同 size 的文件对不会被一刀切跳过 —— 只有同时满足"时长差 ≤ strong_tol"的才被跳过（表示已被强候选覆盖）。否则继续在弱候选里判定，避免"同 size 但时长差介于 strong_tol 和 weak_tol 之间"的漏报。

---

## 推荐保留策略

每组自动计算一个"建议保留"项，用于提示哪个版本质量最高。评分规则：

```
score = (width × height, video_bitrate + audio_bitrate, size, duration)
```

按元组字典序取最大：**分辨率优先 → 总码率 → 文件大小 → 时长**。在控制台输出、CSV、HTML 中都会用 ★ 标记 / 绿色高亮显示。

> ⚠️ 该策略是启发式，最终保留决策请以自己的实际用途为准。例如某些高质量压缩版可能评分略低但体积小很多。

---

## 完整参数

### 源选择（互斥，必填）

| 参数 | 说明 |
|------|------|
| `folder` | 位置参数。要扫描的文件夹路径 |
| `--drives G-U` | Windows 专用。盘符范围，如 `G-U` 表示依次扫描 G: 到 U: |

### 过滤与阈值

| 参数 | 默认 | 说明 |
|------|------|------|
| `--min-size-mb N` | `100` | 跳过小于 N MB 的文件，加速扫描 |
| `--duration-tol N` | `1.0` | 强候选时长容差（秒） |
| `--weak-duration-tol N` | `2.0` | 弱候选时长容差（秒） |
| `--name-sim N` | `0.8` | 中候选文件名相似度阈值（0~1） |
| `--exclude-dir KEY` | 默认排除 `CHN` | 忽略名称含该关键字的文件夹，**可多次指定**；传 `--exclude-dir ''` 显式关闭 |

### 并发与输出

| 参数 | 默认 | 说明 |
|------|------|------|
| `--workers N` | `min(16, cpu×2)` | 元数据解析线程数 |
| `-o, --output PATH` | `dup_result.json` | JSON 输出路径 |
| `--csv PATH` | 不输出 | 额外输出 CSV |
| `--html PATH` | 不输出 | 额外输出 HTML 可视化报告 |
| `--dry-run` | `false` | 只打印控制台摘要，不写任何结果文件 |

---

## 输出格式

三种格式共享同一套分组数据，每组携带 `group_id`、`file_count`、`wasted_bytes`、`median_size`、`reason`、`suggested_keep` 和文件列表。所有组**预先按 `wasted_bytes` 降序排序**，`group_id` 因此与排序顺序一致（如 `strong_0000` 是强候选里浪费空间最大的组）。

### JSON

完整的结构化数据：

```json
{
  "scan_time": "2026-09-13T21:30:00",
  "roots": ["G:\\"],
  "total_scanned": 15432,
  "failed_files": [
    {"path": "G:/video/corrupt.mkv", "reason": "missing_duration"},
    {"path": "G:/video/broken.mp4",  "reason": "parse_error: MediaInfoException"}
  ],
  "strong_candidates": [
    {
      "group_id": "strong_0000",
      "reason": "大小完全相同 + 时长误差 ≤ 1.0s",
      "file_count": 3,
      "median_size": 8589934592,
      "wasted_bytes": 17179869184,
      "suggested_keep": "G:/video/keep_this.mkv",
      "files": [
        {"path": "...", "name": "...", "size": 8589934592, "size_mb": 8192.0,
         "duration": 7245.312, "resolution": "1920x1080",
         "video_bitrate": 8000000, "audio_bitrate": 192000, "format": "Matroska"},
        {"path": "...", "name": "...", "...": "..."}
      ]
    }
  ],
  "mid_candidates":  [...],
  "weak_candidates":  [...]
}
```

### CSV

每行一个文件，**UTF-8 with BOM**（Excel 直接双击可打开）。包含列：

```
category | group_id | wasted_mb | file_count | suggested_keep |
name | path | size_mb | duration_s | resolution |
video_bitrate | audio_bitrate | format
```

若存在读取失败的文件，会在同目录额外生成一个 `*_failed.csv`，含 `path` + `reason` 两列。

### HTML

单文件内联样式，直接浏览器打开。按强 / 中 / 弱分节，每组一个卡片：

- 顶部 badge 用红 / 橙 / 黄区分三档
- 组标题显示 `group_id`、浪费 MB、原因、文件数
- 建议保留的文件整行绿色高亮
- 顶部汇总扫描时间、文件数、**预计可释放总空间（GB）**

---

## 性能与库模式

### 库模式 vs 子进程模式

pymediainfo 会优先尝试加载 `libmediainfo` 动态库。成功时（库模式）每个文件只是一次 `MediaInfo.parse()` 调用，线程池收益极高。失败时退化为对每个文件启动一个 `mediainfo` 子进程，此时：

- 启动开销大，多线程帮不了太多
- 线程数调太高反而进程调度开销占主导

**建议**：安装库版本（Windows 放 DLL、Linux 装 `libmediainfo0v5`），用 `--workers 16~32` 通常能跑到数千文件/秒。

### 分批提交

扫描阶段以 **2000 个文件** 为一批提交给线程池，避免一次提交 10 万个 Future 造成内存峰值。

### 文件名归一化缓存

`normalize_name` 用 `@lru_cache(maxsize=65536)` 缓存，进入分组前还会对每个元数据预计算一次 `_norm_strip`，双层循环里用 `f.get("_norm_strip")` 直接取，避免重复构造缓存键。

### name_similarity 安全剪枝

调用 `SequenceMatcher` 之前先用数学必要条件排除必然低于阈值的字符串对：

```
若 |la - lb| > (1 - threshold) × (la + lb) + 1  → ratio 必定 < threshold，直接返回 0
```

这把大部分"一看就不可能相似"的字符串对挡在 `SequenceMatcher` 外面。

---

## 常见问题

**Q: 为什么我的文件明明一样却分到了弱候选甚至没命中？**
A: 强候选要求 **大小完全相同**。某些编码容器（MKV vs MP4）即使内容完全一样，文件大小也可能有几十 KB 差异。可以把 `--duration-tol` 和 `--min-size-mb` 调小试试，或检查元数据提取是否成功（`failed_files` 里有没有 reason）。

**Q: `--drives` 在 macOS / Linux 上能用吗？**
A: 不行。非 Windows 平台 `--drives` 会直接报错退出。用位置参数 `folder` 指定具体目录。

**Q: 为什么 `failed_files` 里有很多 `missing_duration`？**
A: 部分损坏文件或流媒体容器（某些 `.ts` / `.m4s`）MediaInfo 能读到 General 轨道但读不到 duration。这类文件会被整个丢弃，不会进入任何候选分组。

**Q: 并查集合并会不会把不应该合并的文件合在一起？**
A: 它只在 **大小完全相同** 的桶内做并查集，合并条件是任意两文件时长差 ≤ tol。如果你把 tol 设得太大（比如 10 秒），确实可能把不同内容合在一起。建议 tol 保持在 1~2 秒。

**Q: CSV 里 `suggested_keep` 是布尔值，Excel 显示 TRUE/FALSE 吗？**
A: 是的。Excel 双击打开即可筛选 `TRUE` 行找到每组建议保留的文件。