# Changelog

所有重要变更都会记录在此文件中，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

## [0.4.0] - 2026-09-13

本次更新围绕「Web 化 + 可运维」补齐配置、进度、安全、结果展示四大块能力，新增 pytest 覆盖至 52 个。

### 新增 — 配置页（`web_index.html`）

- **配置预设（F1）**：顶部下拉保存多套配置（如"全盘快速扫"、"单盘精细扫"、"电影专用"），一键切换，存于 `localStorage`
- **卡片级重置（F2）**：每张卡片右上角 `↺` 按钮，单独恢复该卡片默认值
- **实时校验（F3）**：路径格式、端口/数值范围等边填边提示，无需等点"开始扫描"才报错
- **主题切换（F4）**：右上角三态 toggle（跟随系统 🖥️ / 亮色 ☀️ / 暗色 🌙），`localStorage` 持久化
- **中英双语（F5）**：纯前端 i18n 字典（`I18N.zh` / `I18N.en`），页面文字即时切换
- **快捷键（F6）**：`Ctrl+Enter` 直接启动扫描，`Esc` 从进度页退回配置页
- **一键生成 `web.bat`（Q1）**：`GET /web.bat` 下载启动脚本，双击即开浏览器 + 启服务
- **移动端适配（Q3）**：配置页在窄屏下重排布局

### 新增 — 进度与状态（P1–P6）

- **ETA 预计剩余时间（P1）**：基于当前速率推算，环形图旁显示"预计 12 分钟"
- **实时速率（P2）**：`1,234 文件/秒`，EMA 平滑，动态刷新
- **四段进度条（P3）**：`enumerate → parse → grouping → saving` 各自独立进度与状态
- **当前盘符/文件（P4）**：parse 阶段显示"正在解析 G:\Videos 第 12345/50000 个：xxx.mkv"
- **暂停 / 继续 / 取消（P5）**：`POST /api/pause|resume|cancel`，通过 `ScanControl` 优雅停线程；取消后仍展示已解析的部分结果
- **任务历史列表（P6）**：侧边栏列出历史扫描任务（含状态 badge），点击重新打开旧结果；`GET /api/history`、`DELETE /api/history/<id>`

### 新增 — 后端与算法

- **`--no-strong` / `--no-mid` / `--no-weak`（B2）**：只跑指定档候选，大文件扫描时可跳过弱候选加速
- **分组前采样预览（B3）**：`preview_scan()` + `POST /api/preview`，先返回候选量/预计耗时，用户确认后再解析
- **扩展名白/黑名单（B4）**：`--ext mp4,mkv` 只扫指定后缀，`--exclude-ext ts` 跳过指定后缀
- **元数据缓存 / 增量扫描（B5）**：按 `path + size + mtime` 命中缓存跳过解析，`--cache-file` / `--no-cache` 控制
- **HTTP 基础认证（B6）**：`python web.py --auth user:pass` 保护全部接口
- **扫描后自动跳结果页（B1）**：后端完成后前端自动 redirect，无需手动刷新

### 新增 — 结果展示（`index.html`）

- **批量"除保留外全选"（R1）**：工具栏一键对所有组执行，除建议保留外全部标记删除
- **预览缩略图（R2）**：后端 `GET /api/thumb` 用 FFmpeg 抽帧（`-ss 5 -frames:v 1 -vf scale=320:-1`），按 `md5(path+size+mtime)` 缓存到 `_thumbs/`；前端懒加载，未装 FFmpeg 时优雅降级为占位图
- **打开文件 / 定位文件夹（R3）**：每行 `📂 打开` 按钮，`POST /api/open` 在资源管理器中定位文件（Windows `explorer /select,` / macOS `open -R` / Linux `xdg-open`）
- **三种排序（R4）**：顶部排序切换 —— 按浪费空间 / 组数 / 平均浪费
- **导出 Dry-run 预览（R5）**：导出删除脚本前先弹确认列表（前 500 条），确认后再下载
- **跨扫描对比（R6）**：两次 JSON 结果 diff，显示"本次新增重复组 N 组 / 新增可释放 X"
- **结果页书签（Q2）**：结果页"存为书签"，下次直接从下拉打开对应 JSON

### 新增 — 安全与稳定

- **路径白名单（S1）**：`--allow-root D:\` 限制只扫指定盘，防止误扫系统盘（`filter_allowed_roots`）
- **单实例文件锁（S2）**：`.web.lock` 记录 PID，配合存活检测防止多开 Flask；`--no-lock` 可关闭
- **日志落盘（S3）**：扫描进度与异常写入 `scan_YYYYMMDD.log`
- **扫描时阻止休眠（Q4）**：Windows 用 `SetThreadExecutionState`，Linux 用 `systemd-inhibit`

### 测试

- 新增 6 个测试类共 22 个用例：`TestNormalizeExtSet` / `TestFilterAllowedRoots` / `TestScanControl` / `TestMetaCache` / `TestEnumerateExtFilter` / `TestPreviewScan`
- 测试总数 30 → **52**，`python -m pytest test_mediadupfinder.py -q` 全绿

## [0.3.4] - 2026-09-13

### 变更

- **`delete_from_json.py` 彻底移除交互式确认**：去掉 `--go` 参数和逐个 `input()` 确认，`--yes` 即直接删除。dry-run（无 `--yes`）仍只预览不删除
- **网页导出时间戳改用电脑本地时区**：之前 `toISOString()` 输出 UTC 时间，现在 `toLocaleString('sv-SE')` 输出本地时区，文件名与电脑时间一致（Python 端本来就是本地时区，现已统一）

## [0.3.3] - 2026-09-13

### 新增

- **`delete_from_json.py` 批量删除脚本**：从 JSON 清单读取路径，支持 dry-run 预览 / 逐个确认 / `--go` 一键删除 / 缺失文件日志 / 失败日志
- **删除列表自动去重**：`--dedup` 默认开启，对重复路径只删一次并报告去重统计（如 `发现 299 条重复，去重为 54 条`）
- **一键生成定位脚本**：网页端新增"生成 Open .py / .bat / .sh"按钮，在资源管理器中逐个定位标记文件（绕过 Windows MOTW 安全限制）

### 修复

- **关键修复：删除列表重复膨胀**：同一个文件因匹配多个重复组（如 `EBOD-192-U.mp4` 出现在 32 个不同组）被导出上百次。`index.html` 的 `getMarkedFiles()` 已用 `Set` 按路径去重，`delete_from_json.py` 也内置二次去重，双重保险
- **MKV 文件码率显示为 0**：`pymediainfo` 不提供 MKV 视频轨 `bit_rate`，回退到 General 轨 `overall_bit_rate`，仍无则用文件大小/时长估算
- **推荐保留按钮优先级错误**："已标记删除"（红色）应高于"推荐保留"（绿色），修复后点击切换状态立即反映正确按钮样式
- **UnicodeEncodeError**：删除脚本去掉 emoji 字符，改用 ASCII 标记（`[!]` `=>` `[x]`），兼容 GBK 终端

### 优化

- 点击文件名复制完整路径，点击路径行复制所在文件夹路径，点击标题复制文件夹路径
- 默认全部展开所有分组，无需手动点"全部展开"

## [0.3.2] - 2026-09-13

### 新增

- **网页端推荐保留策略可切换**：toolbar 新增下拉框，6 套打分方案实时切换无需重扫
  - 分辨率 → 码率 → 大小（默认，与后端一致）
  - 大小 → 分辨率 → 码率（大文件优先）
  - 码率 → 分辨率 → 大小（高码率优先）
  - 分辨率 → 大小 → 码率（画质面积优先）
  - 码率 → 大小 → 分辨率
  - 大小 → 码率 → 分辨率

### 优化

- **默认输出文件名带时间戳**：未显式 `-o` 时自动生成 `dup_result_YYYYMMDD_HHMMSS.json`（CSV/HTML 同名），避免覆盖历史结果
- **统一 reason 字段为干净分类标签**：去掉文件名相似度（0.82/0.91…）、分辨率具体值（1920x1080 vs 1280x720）、码率数值、容差秒数等所有具体数值
- 转换前 345 种 reason 变体 → 转换后 **5 种稳定分类标签**，Web UI 筛选下拉干净可用
- 新 reason 对照表：
  - `大小相同 + 时长相近`（强候选）
  - `文件名相似 + 分辨率/码率相同`（中候选 A）
  - `文件名相同 + 分辨率不同` / `文件名相同 + 码率不同` / `文件名相同 + 分辨率不同 / 码率不同`（中候选 B）
  - `时长相近 + 分辨率相同 + 大小不同` / `时长相近 + 分辨率相同 + 大小相同`（弱候选）
- 建议保留逻辑简化：分辨率 > 总码率 > 文件大小（大的优先），去掉时长 tie-break
- 用新逻辑重建 `dup_result.json`（G-N 扫描，19139 文件，131890 组），就地转换 reason 和 suggested_keep

## [0.3.1] - 2026-09-13

### 修复

- **关键修复：合并全部三档候选**：之前 `index.html` 只加载 `strong_candidates`（40 组），完全忽略了 `mid_candidates`（5809 组）和 `weak_candidates`（126041 组）。现在合并全部三档并按浪费空间排序，共 131,890 组完整显示
- **关键修复：`worker-stream.js` 简化为直接 `JSON.parse`**：之前的流式正则提取逻辑有缺陷，只抽取 `strong_candidates` 且 brace 匹配偶尔提前终止。实际上在 Worker 里跑 `JSON.parse` 本身不阻塞主线程，无需花哨的流式方案
- 修复 `file://` 协议下 Worker 静默失败导致进度卡在 2% 的问题
- 新增 **5 秒启动超时**：Worker 首条消息未收到立即回退主线程（之前要等 120 秒）
- Worker 创建失败（`try/catch`）和 `onerror` 也改为自动回退主线程，不再弹 alert 重置 UI

### 新增

- **分页系统**：用"上一页 / 下一页 + 页码 + 省略号"替代原来的"加载更多"分批渲染，浏览 13 万组也流畅
- **分类筛选下拉**：支持按"强候选 / 中候选 / 弱候选 / 全部"快速过滤
- **分组分类 badge**：每组头部显示彩色分类标签
- **统计卡片分类明细**：主卡片副标题显示"强 N · 中 N · 弱 N"

## [0.3.0] - 2026-09-13

### 新增

- **Web 可视化界面 `index.html`**：浏览器直接打开，拖拽/点击导入 JSON 结果，支持 Worker 异步解析 + 分批渲染超大文件
- **JSON Worker (`worker-json.js`)**：小文件（< 200MB）走 Web Worker 后台解析，不阻塞主线程
- **流式 Worker (`worker-stream.js`)**：大文件走流式分块读取 + 正则提取 `strong_candidates`，避免一次性 `JSON.parse` 内存爆炸
- **智能降级**：Worker 超时（120s）自动回退主线程解析
- **统计卡片**：重复分组数、可释放空间、平均浪费、最大浪费组
- **分组折叠/展开**、搜索、按原因筛选、全部展开/折叠、清空标记
- **智能建议保留**：每组自动标记推荐保留文件，支持"除保留外全选删除"
- **导出功能**：标记的删除列表可导出 TXT / CSV / JSON 三种格式

### 变更

- 版本号升级至 0.3.0

### 修复

- 修复 `file://` 协议下 Worker 静默失败导致进度卡在 2% 的问题
- 新增 **5 秒启动超时**：Worker 首条消息未收到立即回退主线程（之前要等 120 秒）
- Worker 创建失败（`try/catch`）和 `onerror` 也改为自动回退主线程，不再弹 alert 重置 UI

## [0.2.0] - 2026-09-13

### 新增

- 默认同时输出 JSON / CSV / HTML 三种格式（`-o result.json` 自动生成同名 `.csv` 和 `.html`）
- `--no-csv` / `--no-html` 参数，按需跳过 CSV 或 HTML 输出
- `--version` / `-V` 参数，输出版本号
- `__version__` 常量
- 单元测试 `test_mediadupfinder.py`，覆盖归一化、相似度、聚类、评分等纯逻辑（30 tests）
- GitHub Actions CI：flake8 + pytest + pymediainfo 依赖安装
- 首个 GitHub Release `v0.1.0`

### 修复

- 修复 README.md 中文乱码（UTF-8 双重编码损坏）
- README 内容重写，覆盖源码全部功能与参数

## [0.1.0] - 2026-09-13

### 新增

- 媒体文件元数据读取（时长、分辨率、码率、格式、文件大小）
- 三级候选分组：强候选 / 中候选 / 弱候选
- 传递闭包聚类，支持多文件互为副本
- 数学剪枝 + 安全剪枝优化
- 线程池并发扫描
- `--drives G-U` 盘符范围扫描
- `--exclude-dir` 排除目录关键字
- 评分机制自动推荐"建议保留"
- JSON / CSV / HTML 三种输出格式（v0.1 需显式指定 `--csv` / `--html`）