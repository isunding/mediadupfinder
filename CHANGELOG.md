# Changelog

所有重要变更都会记录在此文件中，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)。

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