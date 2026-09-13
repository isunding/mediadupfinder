# 贡献指南

感谢你对 mediadupfinder 感兴趣！欢迎提交 Issue 和 Pull Request。

## 开发环境

```bash
# 1. Fork 仓库，然后克隆
git clone https://github.com/<your-username>/mediadupfinder.git
cd mediadupfinder

# 2. 创建虚拟环境（推荐）
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

# 3. 安装依赖
pip install pymediainfo flake8 pytest

# 4. 安装 MediaInfo CLI（各平台见 README.md）
```

## 代码规范

- **Python 3.8+ 兼容**
- 遵循 PEP 8，flake8 不报错即可
- 单行不超过 100 字符
- 字符串统一用双引号（除非必要）

## 测试

```bash
pytest test_mediadupfinder.py -v
```

CI 会自动在每个 PR 上运行 flake8 + pytest，**必须全部通过**。

## 提交规范

Commit message 采用以下前缀：

| 前缀 | 用途 |
|------|------|
| `feat:` | 新功能 |
| `fix:` | Bug 修复 |
| `refactor:` | 重构（不改行为） |
| `perf:` | 性能优化 |
| `test:` | 新增或修改测试 |
| `docs:` | 文档变更 |
| `chore:` | 构建 / CI / 杂项 |

## Pull Request 流程

1. Fork 并创建分支（`feat/xxx` 或 `fix/xxx`）
2. 本地写代码、跑测试
3. 提交 PR，说明：**改了什么 / 为什么改 / 怎么验证的**
4. 等待 Review，合并后 CI 自动发布

## 发布流程（维护者）

```bash
# 1. 更新 mediadupfinder.py 中的 __version__
# 2. 更新 CHANGELOG.md
# 3. 打 tag 并推送
git tag -a vX.Y.Z -m "vX.Y.Z - 简短说明"
git push origin main --tags

# 4. 创建 GitHub Release
gh release create vX.Y.Z \
  --title "mediadupfinder vX.Y.Z" \
  --notes-file CHANGELOG.md
```

## 行为准则

- 尊重不同意见
- 保持友善、建设性的讨论
- 如有争议，由维护者最终裁决