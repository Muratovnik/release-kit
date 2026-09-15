# 审计策略、范围与私有 overlay

[英文版](../audit.md)是规范版本。第一次检查请使用
[最小 CLI 配置](../../README.zh-CN.md#在既有项目中的第一次检查)。`audit` 命令把项目规则、
Betterleaks 密钥扫描与 Lychee 离线本地链接检查组合在一起。`exposure` 和 `overlay` 是更
狭窄的诊断命令，不能替代组合式的发布门禁。

内置文本规则匹配具体模式、对选定文本做归一化，并应用所有者声明的精确值与正则表达式。
它们不进行通用的语言理解，也不会把工作树或私有策略上传给任何 LLM。一次通过只能说明：在
被检查的范围内，已配置的检查通过了。

## 配置

`relkit.toml` 属于被守护的仓库。下面是一个**扩展示例**，并不是必须采用的默认配置；请只
声明对你的项目有意义的规则。

```toml
[exposure]
private_paths = [".private", ".codex"]
private_files = ["AGENTS.local.md", ".mcp.json"]
private_suffixes = [".local.md"]
required_ignores = [".private", ".codex", "AGENTS.local.md", ".mcp.json"]
allowed_users = ["example-owner"]
allowed_identities = [
  "Example Maintainer <maintainer@example.invalid>",
  "dependabot[bot] <49699333+dependabot[bot]@users.noreply.github.com>",
]
forbid_png_metadata = true
forbid_ai_attribution = true
forbid_internal_planning = true
forbid_machine_observations = true
provenance_required = ["tests/generated/*", "docs/screenshots/*"]
betterleaks_config = ".betterleaks.toml"

# Deliberate synthetic fixtures where structural rules are not meaningful.
# This does not suppress the secret scanner or non-excludable owner findings.
exclude = ["tests/fixtures/*"]

# Adoption debt can only shrink; a stale entry also fails.
[exposure.baseline]
"legacy/config.toml" = ["home-directory"]

# A public provider name is allowed only on declared product surfaces.
[exposure.providers.Someservice]
role = "product-data-provider"
allowed_surfaces = ["src/*", "docs/*", "tests/provider/*"]

# These declarations record review; they do not anonymize or generate files.
[exposure.provenance]
"tests/generated/*" = "synthetic"
"docs/screenshots/*" = "anonymized"
```

供应商的名称并不会仅仅因为所有者使用了它的服务而变成私有信息。供应商角色的词表是封闭
的：仅属于所有者的工作流不能被重新标注为产品供应商。被声明为 `machine-derived` 的材料
仍然算作问题，必须在发布前替换；`anonymized` 是项目对「已完成复核」的断言，而不是自动
匿名化的保证。

默认配置启用密钥与链接检查。常规的 `.betterleaks.toml` 在所维护的默认规则之上扩展：

```toml
title = "project secret scanning"
betterleaksMinVersion = "1.8.1"

[extend]
useDefault = true
```

密钥白名单要保持狭窄，针对具体的规则、路径或取值。结构性的 `exclude` 模式不是密钥扫描器
的白名单。基线记录的是既有的采用债务；排除项描述的是某条结构性规则不适用的位置。
`--strict` 还会因为仍然存在基线而判定失败。无效或过期的基线条目不会造就一个永久为绿的
例外。

## 范围

```text
python .github/relkit.pyz audit
python .github/relkit.pyz audit --staged
python .github/relkit.pyz audit --history
```

工作树范围包括被跟踪的文件，以及未被跟踪但也未被忽略的待发布候选文件。暂存范围读取 Git
索引：`relkit.toml`、Betterleaks 配置及其相对路径的策略文件都必须已入索引。未暂存的策略
改动不属于该结论。Betterleaks 扫描已暂存的改动；策略与 Markdown 检查索引内容。

历史范围需要干净的**被跟踪**工作树和完整的本地历史。它检查当前 `HEAD`、本地与远端分支、
标签、Git notes，以及已拉取的 GitHub pull request、GitLab merge request 和 Gerrit change
引用。引用名称、附注标签的消息与打标签者身份，与提交和 blob 一同被检查。客户端生成的合成
检查点引用被刻意排除在此范围之外。未被跟踪但也未被忽略的文件仍可由工作树候选检查覆盖；
它们并不会因此成为 Git 历史的一部分。历史检查拒绝浅克隆、replace 引用与非空的 Git graft，
并在其 Git 与扫描器进程中关闭 replace 语义。

在做恢复性复核之前，请先拉取仅存在于托管端的引用：本地检查无法检查克隆中并不存在的对象。
Git 历史扫描及其链接检查都不覆盖托管的 issues、Actions 日志和单独上传的发布文件。这些独立
的发布面参见 [publication review](../publication-review.md)（英文）。

CI 通常使用不带 `--owner` 的 `--history`：公开克隆无从得知私有的所有者取值。在所有者已复核
的安装上使用：

```text
python .github/relkit.pyz audit --history --owner
python .github/relkit.pyz audit --history --owner --require-overlay
```

所有者模式额外要求私有策略和一份有效的已安装防护。`--require-overlay` 还要求精确的挂载
链接清单。
[基础的公开策略审计](../../README.zh-CN.md#在既有项目中的第一次检查)并不需要这些参数。

### 压缩包与外部对象

ZIP 系列容器在工作树、索引和可达历史中按内容识别：`.zip`、`.whl`、`.jar`、`.pyz`、
Office/OpenDocument 包，以及没有扩展名的 ZIP。不安全的条目路径或符号链接、格式错误或超出
预算的容器、私有路径以及命中文本规则的内容都会导致失败，其中包括有界的嵌套 ZIP 和 Unicode
条目。诊断信息会同时指出外层构件与具体条目。

启用 `forbid_png_metadata` 后，它还会检查 PNG 条目以及可达历史中的 PNG 与压缩包。它的结构
性排除项依然适用。

不受支持的压缩格式（包括 tar/gzip、7z 与 RAR）采取失败关闭策略，而不是对未解码的字节给出
结论。格式错误或超出预算的已声明 ZIP 属于不可排除的检查失败。

Git LFS 指针与子模块 gitlink 会失败，因为它们的外部内容并不包含在被检查的 Git blob 中。
请把外部仓库当作独立的根来审计；LFS 材料需要显式复核或迁移。这类失败以及私有所有者策略的
问题，都不能通过基线或结构性排除来接受。

`--no-download` 会把缺失的已校验引擎缓存视为一次运行失败。关于平台固定、缓存完整性与显式
指定可执行文件，请阅读
[distribution details](../distribution.md#scanner-platform-and-cache-behavior)（英文）。

## 私有所有者策略

私有拓扑、精确的禁止取值、所有者工作流与个人数据表达式都不应出现在公开配置中。所有者模式
按以下顺序确定策略根目录：

1. 非空的 `RELKIT_PRIVATE_ROOT`。相对取值保持原有的兼容行为，相对公开克隆的父目录解析。
2. 公开仓库本地 Git 配置中唯一的 `releasekit.privateRoot` 取值。相对取值相对公开克隆的根
   目录解析。
3. 约定的同级目录 `../<克隆名>-private`。

面向所有者与 overlay 的 CLI 命令、已安装的所有者防护、协调器的所有者审计以及 MCP 的所有者
请求，都使用同一套选择顺序。

在不新增被跟踪项目文件的前提下，为仓库设置持久选择：

```text
git config --local releasekit.privateRoot "<absolute-owner-policy-path>"
```

全局与系统级 Git 配置以及被包含的文件都不是策略来源。本地键缺失时允许回退到同级目录。
重复、为空、格式错误或不可读的本地取值会使所有者检查被拒绝；配置指向的目录不存在时同样如
此。无效的显式选择绝不会被同级目录回退所替代。对于非 Git 根目录，本地配置查找会被跳过，而
环境变量与同级目录两种方式在没有 Git 的情况下依然有效。
链接工作树共享仓库级本地配置，因此当所有工作树都必须选中同一份策略时，请使用绝对路径。
相对的本地取值被刻意按各自工作树的公开根目录解析。

选中的根目录拥有 `.publication-private-values`，也可以拥有一份 `install.conf.yaml` 链接
清单。精确取值可能包含工作区名称、私有命名空间、记录标识符和路径片段；不要公开这份列表。

同一个根目录还可以包含 `.publication-owner.toml`：

```toml
version = 1
owner_workflows = ["OwnerWorkbench"]

[[patterns]]
name = "private-record-reference"
kind = "personal-data"
expression = '''\brec_[0-9a-f]{12}\b'''

[[patterns]]
name = "local-inventory-observation"
kind = "machine-observation"
expression = '''(?i)observed on the maintainer's machine'''
```

模式的种类为 `owner-workflow`、`personal-data` 与 `machine-observation`。所有者模式至少需要
一个精确取值、工作流或模式；空的或缺失的策略无法仅凭结构性检查给出所有者模式的通过结论。
这两个所有者策略文件名，一旦出现在公开工作树或压缩包中，就无条件视为私有。

人类作者信息、许可证元数据以及项目自身的公开身份，都不属于匿名性问题。什么不能外流由所有
者决定。扫描器不能仅凭文本中出现了某个名称就替他做这个决定。

## 仓库防护

只有在复核过防护的输入、并取得单独所需的改动钩子许可之后，才安装它：

```text
python .github/relkit.pyz protect install --dry-run
python .github/relkit.pyz protect install --plan-hash REVIEWED
python .github/relkit.pyz protect check
```

已安装的防护位于 `<git-common-dir>/hooks/pre-push`，运行带强制私有所有者策略的历史门禁。
基础 CLI 安装并不需要它。它会固定被跟踪的投影副本、`relkit.toml`、所配置的密钥策略，以及
——对于基于 workflow 的发布配置——发布用的 workflow。对已固定输入的复核性改动需要显式的
[防护刷新](updates.md#已被修改的文件与防护漂移)。

如果 `core.hooksPath` 把 Git 重定向到别处，那位外部所有者必须先提供一个带
`# git-common-dir-hook-dispatcher: pre-push v1` 标记的可执行调度器。release-kit 会先验证
兼容性再写入自己的仓库防护；它从不创建或替换外部调度器。防护会解析 `python`、`python3` 或
`py`。较旧但仍受支持的防护模板会继续执行其固定项，直到被显式刷新。关于 0.5.0 之前的归属
模型，请遵循[迁移流程](updates.md#迁移防护钩子的所有者)。

本地的 pre-push 防护不是服务端的分支保护。不要绕过它，也不要为了让发布通过而削弱所有者
策略。

## Overlay 契约

私有同级仓库中的 Dotbot `install.conf.yaml` 是唯一的挂载列表。release-kit 会校验每一条
`target: source` 挂载确实以符号链接或 Windows junction 的形式存在、解析到清单中**精确**的
源、在公开侧被忽略且未被跟踪，并且指向私有仓库所跟踪的路径。仅仅解析到私有根目录内部的
某处并不够。

链接由 Dotbot 创建，release-kit 只负责校验。采用复制或同步式 overlay 的项目也可以独立采用
这些发布检查；改变归属与挂载模型属于另一次迁移。`relkit overlay` 只诊断这个契约，不做任何
修复。

### 清单格式与 YAML 兼容性

早先按行解析的 YAML 读取器可能会遗漏后面某个包含注释、带引号键或流式映射的块。它已不再
使用。现在会先完整解析整个文档，然后才返回任何挂载；文档中任何一处不受支持的链接都会导致
整份列表被拒绝。

JSON 使用 Python 标准库处理。Dotbot 同时接受 JSON 与 YAML，因此同一份已复核的
`install.conf.yaml` 可以写成 JSON，而无需引入第二份列表，也无需改变 release-kit 发现私有
根目录的方式。例如：

```json
[{"link": {"../public/.someclient": "local/.someclient"}}]
```

对于已有的 YAML，请把 `overlay-yaml` 额外依赖从一份已复核的源码检出安装到真正执行 CLI 的
那个隔离 Python 环境中：

```text
python -m pip install "<reviewed-source-checkout>[overlay-yaml]"
```

该额外依赖固定 PyYAML 6.0.3。它的安全加载器负责处理 YAML 语法；release-kit 只校验显式、
无条件的「目标—源」配对。重复的映射键、隐式或扩展目标、条件与 glob 形式的链接默认值，以及
环境变量与家目录展开，都会被拒绝，而不是得到一个部分结论。该加载器绝不执行 Dotbot 命令，
也不执行 YAML 中的 Python 对象标签。缺少 PyYAML 是一个需要处理的错误，而不是忽略该块的
许可。基础 CLI 命令与 JSON overlay 不需要这个额外依赖。

**兼容性说明：** 仅升级 zipapp 并不会安装 PyYAML。在采用此项变更之前，请确认实际执行的解释
器，包括钩子所使用的解释器。已构建插件那套既有的锁定 SDK 环境中不包含 PyYAML：这条路径请在
共享清单中使用 JSON，或者单独安装带 `[mcp,overlay-yaml]` 的独立适配器。不要修改已安装插件的
锁文件，也不要用未经复核的全局安装来掩盖该错误。任何清单都不会被自动改写或重命名。可选的
YAML 转 JSON 原地迁移必须完整保留整份 Dotbot 文档，而不只是其中的链接，并且必须是一次显式
的、经所有者复核的改动。

这项依赖被刻意设计为可选，而不是内置一套 YAML 的重新实现。参见
[Dotbot 配置格式](https://github.com/anishathalye/dotbot#configuration)与
[PyYAML 安全加载](https://pyyaml.org/wiki/PyYAMLDocumentation)。

## 诊断接口与自动化

`relkit exposure` 只运行内置的策略检查，保留它是为了诊断与兼容。组合式的发布/CI 检查请使用
`audit`。发布说明的提取与校验被单独拆出，因为它们需要一个明确请求的版本号；参见
[notes](../notes.md)（英文）。每条命令都可以选择输出
[结构化 CLI 结果](../cli-json.md)（英文），其退出码与状态语义属于公开接口的一部分。配置或
执行错误并不构成「安全」的证据。
