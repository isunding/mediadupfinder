# mediadupfinder

按元数据分组的媒体文件查重工具。扫描文件夹或盘符，基于 **大小、时长、分辨率、码率、文件名** 等特征自动识别重复/近似文件对，输出结构化报告，按可释放空间降序排列，优先清理高价值目标。

## 功能概览

- **三级候选分组**：强 / 中 / 弱，覆盖从"几乎确定重复"到"可能是同一内容的不同版本"
- **全局去重**：三档之间共享已认领的文件对，不交叉重复报告
- **传递闭包聚类**：同大小文件按时长做并查集合并，避免链式相近被拆开
- **浪费空间估算**：按 `(文件数 - 1) × 中位大小` 估算每组可释放空间，全量按此降序
- **多格式输出**：JSON（默认带时间戳 `dup_result_YYYYMMDD_HHMMSS.json`，避免覆盖）+ CSV + HTML，`--dry-run` 只打印不写文件
- **Web 可视化界面**：`index.html` 浏览器直接打开，拖拽导入 JSON，Worker 异步解析超大文件、分页浏览（13 万组也流畅）、6 套推荐保留策略实时切换、分类筛选、导出删除清单
- **多排除关键字**：`--exclude-dir` 可多次指定，空串显式关闭
- **扩展名白/黑名单**：`--ext mp4,mkv` 只扫指定后缀，`--exclude-ext ts` 跳过指定后缀
- **分档开关**：`--no-strong / --no-mid / --no-weak`，只跑某一档候选，大文件时可跳过弱候选加速
- **增量扫描**：元数据按 `path + size + mtime` 缓存（`--cache-file` / `--no-cache`），重复扫描只解析新增/修改文件
- **采样预览**：`--preview` 先枚举候选并估算耗时，确认后再实际解析
- **路径白名单**：`--allow-root D:\` 限制只能扫指定盘，防止误扫系统盘
- **可控并发**：`--workers N` 指定线程池大小，`tqdm` 进度条（若已安装）
- **失败溯源**：读取失败记录原因（`parse_error / no_tracks / missing_file_size / missing_duration`），结束时按原因聚合计数
- **跨平台**：Windows 盘符扫描（`--drives G-U`），macOS / Linux 文件夹模式
- **Web 服务 `web.py`**：Flask + SSE 实时进度，配置页 + 进度页 + 结果页三页联动；支持暂停/继续/取消、任务历史、HTTP 基础认证、单实例锁、日志落盘、扫描期间阻止休眠

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
- [Web 可视化界面](#web-可视化界面)
  - [快速使用](#快速使用)
  - [功能特性](#功能特性)
  - [文件说明](#文件说明)
- [Web 服务（web.py）](#web-服务webpy)
  - [启动](#启动)
  - [配置页功能](#配置页功能)
  - [进度与状态](#进度与状态)
  - [API 一览](#api-一览)
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

可选依赖：

- **`tqdm`**：命令行进度条
- **`flask` + `flask-cors`**：运行 `web.py` Web 服务
- **`ffmpeg`**：Web 结果页的预览缩略图（未安装时显示占位图，不影响其他功能）

脚本本身只有一个文件，直接下载 `mediadupfinder.py` 即可（Web 模式还需 `web.py` / `web_index.html` / `index.html` / `worker-*.js`）。

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

只扫指定后缀 + 跳过弱候选（大文件时更快）：

```bash
python mediadupfinder.py --drives G-U --ext mp4,mkv --exclude-ext ts --no-weak
```

先采样预览再决定是否解析：

```bash
python mediadupfinder.py --drives G-U --preview
```

限制只能扫指定盘（防误扫系统盘）：

```bash
python mediadupfinder.py --drives C-U --allow-root "D:\\" --allow-root "E:\\"
```

启动 Web 界面（推荐，配置/进度/结果三页联动）：

```bash
python web.py
# 然后浏览器打开 http://localhost:5000
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
| `--ext LIST` | 不限 | 只扫描指定后缀，逗号/分号/空格分隔，如 `--ext mp4,mkv`（可加非媒体后缀，如 `--ext txt`） |
| `--exclude-ext LIST` | 无 | 跳过指定后缀，如 `--exclude-ext ts,rmvb` |
| `--allow-root PATH` | 无 | 路径白名单，**可多次指定**；只允许扫描这些根路径下的文件，防止误扫系统盘 |

### 分档与缓存

| 参数 | 默认 | 说明 |
|------|------|------|
| `--no-strong` | `false` | 跳过强候选分组（大小相同 + 时长相近） |
| `--no-mid` | `false` | 跳过中候选分组（文件名相似 / 相同） |
| `--no-weak` | `false` | 跳过弱候选分组（时长接近 + 分辨率相同 + 大小不同），大文件扫描时可显著加速 |
| `--cache-file PATH` | `.mdf_cache.json` | 元数据缓存文件路径 |
| `--no-cache` | `false` | 禁用元数据缓存，每次全量解析 |
| `--preview` | `false` | 只做采样预览：枚举候选文件并估算耗时，不做实际解析 |

> **增量扫描**：默认启用元数据缓存，按 `path + size + mtime` 命中即跳过解析（`MediaInfo` 调用是最耗时的一环）。只修改过或新增的文件会被重新解析，重复扫描同一目录时速度大幅提升。

### 并发与输出

| 参数 | 默认 | 说明 |
|------|------|------|
| `--workers N` | `min(16, cpu×2)` | 元数据解析线程数 |
| `-o, --output PATH` | `dup_result_YYYYMMDD_HHMMSS.json` | JSON 输出路径 |
| `--csv PATH` | 不输出 | 额外输出 CSV |
| `--html PATH` | 不输出 | 额外输出 HTML 可视化报告 |
| `--no-csv` | `false` | 跳过 CSV 输出 |
| `--no-html` | `false` | 跳过 HTML 输出 |
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

## Web 可视化界面

`index.html` 是一个纯前端单页应用（SPA），用于交互式浏览 JSON 扫描结果。

### 快速使用

1. 运行扫描得到 JSON（默认输出 `dup_result_YYYYMMDD_HHMMSS.json`，自动带时间戳不覆盖历史）：
   ```bash
   # 扫描 G-U 全部盘符
   python mediadupfinder.py --drives G-U
   # 或指定文件夹 + 显式输出
   python mediadupfinder.py /some/folder -o result.json
   ```

2. 用浏览器打开 `index.html`，将 JSON 拖入页面或点击选择文件即可。

> 建议用 `python -m http.server 8000` 起本地服务后访问 `http://localhost:8000/index.html`，`file://` 协议下部分浏览器 Worker 可能静默失败（已做 5 秒超时自动回退主线程的兜底）。

### 功能特性

| 特性 | 说明 |
|------|------|
| **拖拽导入** | 支持拖拽或点击选择 JSON 文件 |
| **Worker 解析** | 全部在 Worker 里跑 `JSON.parse`，不阻塞主线程 |
| **智能降级** | Worker 5 秒无响应立即回退主线程（`file://` 协议友好），120 秒硬超时兜底 |
| **分页浏览** | 每页 50 组，上一页/下一页/页码/省略号，浏览 13 万组也流畅 |
| **统计卡片** | 重复分组数（含强/中/弱各档明细）、可释放空间、平均浪费、最大浪费组 |
| **搜索 / 筛选** | 按文件名、路径搜索；按重复原因筛选；按分类（强/中/弱候选）筛选 |
| **分组分类** | 每组 header 显示彩色分类 badge |
| **折叠 / 展开** | 全部展开 / 全部折叠，记住手动展开的组 |
| **推荐保留策略** | 下拉切换 6 套打分方案（分辨率→码率→大小 / 大小优先 / 码率优先…），实时重算高亮 |
| **智能标记** | 一键"除推荐保留外全选删除"，或逐组手动切换；工具栏支持对**所有组**批量执行 |
| **排序切换** | 顶部按「浪费空间 / 平均浪费」排序；默认浪费空间降序 |
| **预览缩略图** | 勾选后由后端 FFmpeg 抽帧（5s 处）生成缩略图并缓存，未装 FFmpeg 时显示占位图 |
| **打开文件 / 定位** | 每行「📂 打开」调用系统文件管理器定位到该文件 |
| **导出 Dry-run 预览** | 导出删除清单前先弹确认列表（前 500 条），确认后再下载 |
| **跨扫描对比** | 与上一次扫描结果 diff，显示新增重复组与新增可释放空间 |
| **结果书签** | 结果页「存为书签」，下次从下拉直接打开对应 JSON |
| **删除清单导出** | 标记后可导出 TXT / CSV / JSON 三种格式 |
| **移动端适配** | 窄屏自动隐藏次要列、缩略图缩小，结果页在手机上可正常浏览 |

### 文件说明

| 文件 | 作用 |
|------|------|
| `index.html` | 结果页，单文件含全部 HTML / CSS / 主线程 JS |
| `worker-json.js` | Worker，`file.text()` + `JSON.parse`，结果一次性返回主线程 |
| `worker-stream.js` | 大文件 Worker（≥ 200MB），同样走 `JSON.parse`（Worker 里跑不阻塞主线程） |

---

## Web 服务（web.py）

`web.py` 是 Flask 后端，提供配置页（`web_index.html`）、进度推送（SSE）与结果页（`index.html`）三页联动的完整 Web 体验。适用于不想敲命令行的场景。

### 启动

```bash
# 最简：默认监听 0.0.0.0:5000
python web.py

# 只允许扫描 D 盘和 E 盘 + 开启认证 + 换端口
python web.py --port 8080 --allow-root D:\ --allow-root E:\ --auth admin:secret
```

| 参数 | 默认 | 说明 |
|------|------|------|
| `--host` | `0.0.0.0` | 监听地址 |
| `--port` | `5000` | 监听端口 |
| `--allow-root PATH` | 无 | 路径白名单，可多次指定；不在白名单内的扫描请求会被拒绝 |
| `--auth user:pass` | 无 | 开启 HTTP 基础认证，保护全部接口（部署到公网时强烈建议开启） |
| `--no-lock` | `false` | 不创建单实例锁 `.web.lock` |
| `--debug` | `false` | Flask 调试模式 |

启动后访问 `http://localhost:5000`。页面右上角可下载 `web.bat`，双击即自动启动服务并延迟 2 秒打开浏览器（Windows）；也可直接双击仓库里的 `一键启动Web.bat`，它会顺带检查依赖、MediaInfo 与 FFmpeg。

> 运行期文件按用途分目录存放（首次启动自动创建，旧版散落在根目录的文件会自动迁移；扫描结果仍写入项目根目录）：
>
> | 目录 | 内容 |
> |------|------|
> | `logs/` | 扫描日志 `scan_YYYYMMDD.log` |
> | `history/` | 任务历史 `scan_history.json` |
> | `config/` | 元数据缓存 `.mdf_cache.json`、单实例锁 `.web.lock` |
> | `_thumbs/` | 结果页缩略图缓存 |

### 配置页功能

- **配置预设**：顶部下拉保存/切换多套配置（"全盘快速扫"、"单盘精细扫"、"电影专用"…），存于浏览器 `localStorage`
- **卡片级重置（↺）**：每张卡片单独恢复默认值
- **实时校验**：路径、数值范围等边填边提示
- **主题三态切换**：跟随系统 / 亮色 / 暗色
- **中英双语**：页面右上角一键切换
- **快捷键**：`Ctrl+Enter` 启动扫描，`Esc` 从进度页返回配置页
- **采样预览**：勾选后先枚举候选并估算耗时，确认后再实际扫描
- **帮助提示（?）**：配置预设、扫描范围、基础参数、候选分档、高级阈值、输出选项等 9 处带 `?` 圆圈，悬停或键盘聚焦即显示说明，文案随中英语言切换
- **服务端配置提示**：启动时读取 `/api/config`，展示白名单、是否开启认证、FFmpeg 是否可用

### 进度与状态

- **四段独立进度条**：`enumerate（枚举）→ parse（解析）→ grouping（分组）→ saving（保存）`
- **实时速率与 ETA**：EMA 平滑的"文件/秒"与"预计剩余时间"
- **指标卡固定宽度**：进度 / 实时速率 / 预计剩余 / 已用时 四卡等宽，数字用等宽字形（`tabular-nums`），数值变化时布局不抖动
- **当前盘符 / 文件**：parse 阶段第一行显示"正在解析 I:\ 第 942/20912 个"，文件名单独占第二行并预留固定高度，长短文件名都不会引发换行抖动
- **暂停 / 继续 / 取消**：通过 `ScanControl`（`threading.Event`）优雅停线程；取消后仍展示已解析的部分结果
- **任务历史侧边栏**：列出历史任务及状态，点击直接打开旧结果，支持删除
- **自动跳转**：扫描完成后自动进入结果页
- **扫描期间阻止休眠**：Windows `SetThreadExecutionState`，Linux `systemd-inhibit`

### API 一览

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/start` | 启动扫描任务 |
| `POST` | `/api/pause` `/api/resume` `/api/cancel` | 暂停 / 继续 / 取消当前任务 |
| `GET` | `/api/progress` | SSE 实时进度推送 |
| `GET` | `/api/status` | 查询任务状态快照 |
| `POST` | `/api/preview` | 启动采样预览 |
| `GET` | `/api/preview_status` | 查询预览状态 |
| `GET` | `/api/result` | 最近一次结果 JSON；`?path=dup_result_xxx.json` 指定文件 |
| `GET` | `/api/download/<filename>` | 下载结果文件 |
| `GET` | `/api/download_latest/<ext>` | 下载最近任务的 json / csv / html |
| `GET` | `/api/history` | 历史任务列表 |
| `GET` | `/api/history/<id>` | 单个历史任务元信息 |
| `GET` | `/api/history/<id>/result` | 历史任务结果 JSON |
| `DELETE` | `/api/history/<id>` | 删除历史记录 |
| `GET` | `/api/thumb` | FFmpeg 抽帧生成缩略图（需装 FFmpeg） |
| `POST` | `/api/open` | 在系统文件管理器中打开 / 定位文件 |
| `GET` | `/api/config` | 服务端配置（白名单、认证、FFmpeg 可用性） |

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

**Q: 为什么第二次扫描同一个目录快很多？**
A: 元数据缓存（`.mdf_cache.json`）按 `path + size + mtime` 命中即跳过 MediaInfo 解析，只解析新增/修改的文件。想强制全量重解析可加 `--no-cache`；换缓存文件用 `--cache-file`。

**Q: Web 结果页的缩略图显示不出来？**
A: 需要系统已安装 `ffmpeg` 并在 `PATH` 中（或放在常见安装目录）。可在 `GET /api/config` 的 `ffmpeg` 字段确认是否被识别；未安装时缩略图位置显示占位图，其余功能不受影响。

**Q: `--allow-root` 怎么用？不填会怎样？**
A: 不填则不做限制（可扫描任意路径）。填写后只允许扫描白名单内的根路径及其子路径，其余请求会被拒绝并返回错误。适合把 Web 服务部署到公网时防止误扫系统盘。

**Q: 部署到公网安全吗？**
A: 建议同时开启 `--auth user:pass`（HTTP 基础认证）和 `--allow-root`（路径白名单），并仅在受信任网络内使用。`.web.lock` 会在脚本目录记录 PID，防止多开 Flask。