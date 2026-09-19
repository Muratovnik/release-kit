# release-kit

[![发布版本](https://img.shields.io/github/v/release/Muratovnik/release-kit?label=release&color=blue)](https://github.com/Muratovnik/release-kit/releases)
[![许可证](https://img.shields.io/github/license/Muratovnik/release-kit?color=blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![下载量](https://img.shields.io/github/downloads/Muratovnik/release-kit/total?label=downloads)](https://github.com/Muratovnik/release-kit/releases)

[English](README.md) · [Русский](README.ru.md) · **简体中文**

在发布 Git 仓库之前先检查它。一条命令即可运行密钥扫描、Markdown 链接检查，以及项目为
自身声明的文件与历史规则。它还能准备发布文件、将其投递到目录或 GitHub，并校验已发布的
内容。

## 实际效果

```text
$ relkit audit
relkit audit: passed
```

```text
$ relkit audit
[README.md]:
[ERROR] .../docs/deploy.md (at 3:9) | File not found. Check if file exists and path is correct

🔍 1 Total (in 0s) 🔗 1 Unique ✅ 0 OK 🚫 1 Error

relkit audit: failed
  Lychee failed
```

退出码 `0` 表示该范围内配置的检查全部通过。退出码 `1` 表示存在问题：请阅读给出的路径，
修复或复核它们。退出码 `2` 表示配置或执行失败，这并不是一个干净的安全结论。用于自动化
时请使用 `audit --json` 和[结果契约](docs/cli-json.md)（英文）。

## 安装

一个工具，每个项目一份配置——与其他任何 linter 的形态相同。请按由谁运行来选择形式。

| 形式 | 安装方式 | 版本来源 |
| --- | --- | --- |
| 安装在你的机器上 | `uv tool install <wheel 地址>` | 你的机器 |
| 固定在仓库中 | 提交到项目里的 zipapp | 已提交的字节 |
| Python 项目的 dev 依赖组 | `uv add --dev "release-kit @ <wheel 地址>"` | 该项目的锁文件 |

**对 CI 和 Git 钩子而言，项目内的形式才是权威。** 机器级安装只是便利，它并不能让一次克隆
变得可复现。

### 安装在你的机器上

```text
gh release download --repo Muratovnik/release-kit --pattern 'release_kit-*' --dir .cache/relkit-download
python -c "import glob,hashlib,pathlib,subprocess,sys; w=glob.glob('.cache/relkit-download/*.whl')[0]; e=pathlib.Path(w+'.sha256').read_text().split()[0]; a=hashlib.sha256(pathlib.Path(w).read_bytes()).hexdigest(); sys.exit('checksum mismatch') if a!=e else subprocess.run(['uv','tool','install',w],check=True)"
```

不带标签的 `gh release download` 取用最新发布版本，因此两条命令都不需要写出版本号。第二条
命令在把文件交给 `uv` 之前，会用已发布的校验和验证它；把 `uv` 换成 `pipx` 同样可行。

若不使用 `gh` 安装，则需在 URL 中写明发布版本：

```text
uv tool install https://github.com/Muratovnik/release-kit/releases/download/v0.27.0/release_kit-0.27.0-py3-none-any.whl
```

这条单独的命令无法写成 `latest`：wheel 的文件名必须携带自身版本，而
`releases/latest/download/` 只提供最新那个发布版本的构件。它同时跳过了校验和验证。除非
你是刻意固定版本，否则请优先使用上面的两条命令。

与文件一同发布的校验和只能发现损坏，它不是独立的发布者身份认证。在条件允许时，请用
`gh release verify` 和 `gh release verify-asset` 校验不可变的发布版本。参见
[信任边界](docs/distribution.md#integrity-and-trust)（英文）。

### 固定在仓库中

zipapp 只需要 PATH 中有 Python，因此它适用于任何仓库，包括完全没有 Python 打包的仓库。
它的文件名永不改变，所以可以直接从最新发布版本获取、校验并就位，一组命令在任何 shell 中
都能工作：

```text
curl -fLO --output-dir .cache/relkit-download --create-dirs https://github.com/Muratovnik/release-kit/releases/latest/download/relkit.pyz
curl -fLO --output-dir .cache/relkit-download https://github.com/Muratovnik/release-kit/releases/latest/download/relkit.pyz.sha256
python -c "import hashlib,pathlib,shutil,sys; s=pathlib.Path('.cache/relkit-download'); t=pathlib.Path('.github/relkit.pyz'); e=(s/'relkit.pyz.sha256').read_text().split()[0]; a=hashlib.sha256((s/'relkit.pyz').read_bytes()).hexdigest(); sys.exit('checksum mismatch') if a!=e else None; sys.exit('already installed: use the update guide') if t.exists() else None; t.parent.mkdir(parents=True,exist_ok=True); shutil.copy(s/'relkit.pyz',t)"
```

两个文件必须来自同一个发布版本；只要两次下载之间没有新的发布，`latest` 就能保证这一点。
如果你不愿依赖这个前提，`gh release download --pattern 'relkit.pyz*'` 会在一次请求中把它们
一起取回。

之后以 `python .github/relkit.pyz audit` 运行。请提交该文件；每个项目各自维护一份副本，
更新其中一份不会更新其他项目。后续变更请走[更新指南](docs/zh-CN/updates.md)，而不是直接
覆盖。

## 在既有项目中的第一次检查

请在你要检查的 Git 仓库根目录下操作。这条路径不需要钩子、私有同级目录、MCP 客户端或任何
托管账号。

创建 `relkit.toml`；如果该文件已存在，请谨慎合并：

```toml
[exposure]
betterleaks_config = ".betterleaks.toml"
required_ignores = [".cache"]
```

创建 `.betterleaks.toml`：

```toml
title = "project secret scanning"
betterleaksMinVersion = "1.8.1"

[extend]
useDefault = true
```

在**运行审计之前**把 `.cache/` 加入项目的 `.gitignore`，保留已有规则，并创建该目录，
这样第一次审计就会在任何扫描器向其写入之前先验证「仅目录」的忽略规则：

```text
python -c "from pathlib import Path; Path('.cache').mkdir(exist_ok=True)"
```

这些起始文件原样存放在 [examples/audit](examples/audit/README.md)（英文）中。本示例没有
关闭任何扫描器。然后：

```text
relkit audit
```

第一次审计可能会下载已固定版本的扫描器压缩包。它检查被跟踪的文件，以及未被跟踪但也未被
忽略的待发布候选文件；它不会暂存、提交、安装钩子、打标签、推送或发布。下载内容与校验过的
扫描器应放在 `.cache`，绝不进入 Git。

提交配置之后，再检查完整的本地历史：

```text
relkit audit --history
```

历史检查需要非浅克隆和干净的被跟踪工作树，并且无法检查从未拉取过的引用。已有的历史问题
可能需要所有者单独决定如何处置；不要仅仅为了让检查变绿而改写历史。

## 要求与限制

| 用途 | 要求 |
| --- | --- |
| CLI | PATH 中有 Python 3.11+ 与 Git |
| 机器级安装 | `uv` 或 `pipx`；wheel 本身没有运行时依赖 |
| 完整审计 | 已固定版本的 Betterleaks 与 Lychee 可执行文件；首次审计会下载并校验它们 |
| 完整审计支持的平台 | Linux x64/arm64（GNU 构建）、macOS x64/arm64、Windows x64 |
| GitHub 投递或更新 | GitHub CLI；投递需要 `gh` 2.98.0+、已完成认证以及文档所述的仓库权限 |
| 插件 | Python 3.11+、`uv`、可写的安装目录，以及支持本插件 stdio 配置的本地客户端 |

Python CLI 与外部扫描器的平台要求并不相同。目前不存在已固定版本的 Windows arm64 原生
Lychee 构件。对未知或未固定的扫描器平台，工具会拒绝执行，而不会选用未经校验的二进制文件。
参见[分发与平台细节](docs/distribution.md)（英文）。

审计不是一次全面的安全评估，也不能证明项目可以安全发布。私有名称必须由其所有者声明。内置
文本规则依据的是具体模式，而不是通用的语言理解。审计不会检查 GitHub issues、Actions 日志
或未拉取的历史。LFS 指针、子模块以及不受支持的压缩包会导致相应检查失败。链接工作树不受
更新程序、发布协调器和 MCP 适配器支持。在采用该门禁之前，请先阅读
[范围与排除项](docs/zh-CN/audit.md#范围)。

## 选择下一步能力

| 任务 | 指南 |
| --- | --- |
| 配置私有路径、基线、所有者规则或 overlay | [审计策略与范围](docs/zh-CN/audit.md) |
| 更新项目固定的 CLI、刷新其防护或回滚 | [更新与恢复](docs/zh-CN/updates.md) |
| 校验并导出经过整理的发布说明 | [Release notes](docs/notes.md)（英文） |
| 本地构建并投递到目录或 GitHub | [Local releases](docs/local-releases.md)（英文） |
| 维护 GitHub Actions 发布流程 | [Actions adapter](docs/release-coordinator.md)（英文） |
| 使用面向智能体的插件 | [Install and use the plugin](docs/plugin.md)（英文） |
| 接入其他本地 MCP 客户端 | [Standalone MCP](docs/mcp.md)（英文） |
| 消费机器可读的结果 | [CLI JSON contract](docs/cli-json.md)（英文） |

中文文档的完整索引见 [docs/zh-CN/README.md](docs/zh-CN/README.md)。

## 疑难排查

| 现象 | 下一步检查 |
| --- | --- |
| `No module`、不受支持的 Python 或找不到 `python` | 检查 `python --version`；使用预期的解释器，而不是某个应用自带的旧环境 |
| 扫描器下载或缓存错误 | 检查网络访问与 `.cache/` 忽略规则；`--no-download` 仅在缓存完整且已校验时可用 |
| 缺少 `relkit.toml` 或密钥策略 | 请在预期的项目根目录下运行；对照起始示例检查这两个文件 |
| 历史检查因本地改动而拒绝执行 | 编辑期间使用 `audit` 或 `audit --staged`；在 `--history` 之前提交已复核的改动 |
| 机器级安装与项目固定版本不一致 | CI 与钩子请以项目内形式为准；把 `relkit --version` 与固定副本作对比 |
| 既有投影副本或防护出现漂移 | 使用[更新指南](docs/zh-CN/updates.md)，而不是覆盖或 `--no-verify` |
| 插件未被发现或启动了旧版本 | 按 [plugin troubleshooting](docs/plugin.md#troubleshooting)（英文）处理，包括重新加载原生客户端 |

## 参与贡献与问题反馈

[CONTRIBUTING.md](CONTRIBUTING.md)（英文）说明了隔离的开发环境搭建、基础检查与完整的分发
检查。维护者在公开任何实质内容之前会使用
[distribution guide](docs/distribution.md) 与
[publication review](docs/publication-review.md)（英文）。安全漏洞请按
[SECURITY.md](SECURITY.md)（英文）私下报告。

采用 MIT 许可证，见 [LICENSE](LICENSE)。所维护的引擎保留各自的许可证，并从其官方发布版本
获取，而不是复制进 CLI。
