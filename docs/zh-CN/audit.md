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
owner_identities = ["Example Maintainer <maintainer@example.invalid>"]
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

### 提交身份与署名

`forbid_ai_attribution` 覆盖工作树、文件历史、附注标签和提交。提交中的署名是提交者本人
的声明，因此 `owner_identities` 以 Git 打印的 `Name <email>` 写法声明所有者，此时：

- 所有者作为作者的提交，其消息和头部不得含有 AI 署名；
- 所有者作为作者或提交者的提交，不得把 AI 工具记为作者或提交者；
- 其他任何人的提交，包括托管平台为其 pull request 构建的测试合并，都不做署名检查。
  他们添加的文件仍然会被检查。

至少要有一个声明的身份是历史范围内某个提交的作者。否则一个拼写错误的条目就会让所有提交
免检，所以审计会直接失败。未设置 `owner_identities` 时，消息规则检查每一个提交。

`allowed_identities` 是另一种封闭列表：历史范围内的每个作者、提交者和附注标签的打标签者
都必须在列表中。它适用于没有其他人写入的历史。任何外部贡献者的 pull request，以及任何在
托管平台以自身身份提交的测试合并上运行的 pull request 检查，都会因此失败。

### 私有仓库中的托管 CI

不为私有仓库运行作业的托管平台，仍会为每次推送创建运行，然后拒绝启动其中的作业，于是
每个提交都显示失败，而这其实是已配置的状态。`hosted_ci = "public-only"` 声明这一状态，
此时审计要求 `.github/workflows/*.yml` 中的每个作业，在事件无法证明仓库是公开的情况下
跳过自身：

```yaml
jobs:
  check:
    if: ${{ github.event.repository && !github.event.repository.private }}
```

两个合取项缺一不可，它们可以与其他以 `&&` 连接的条件并列，但不能放在 `||` 之后。定时
事件不携带仓库信息，而托管平台采用宽松比较，因此单独的 `!github.event.repository.private`
在那里为真，会被拒绝。带有该条件的作业会被跳过而不是失败。仓库公开后，它会在除定时之外
的所有事件上重新启动。审计无法读取其结构的作业，例如流式映射或合并键，会以 `hosted-ci`
失败而不是通过。只检查当前工作树。

默认配置启用密钥与链接检查。项目不需要 `.betterleaks.toml`：没有它时，Betterleaks 使用其所维护的默认
规则扫描，且规则是显式选定的，因此结果不依赖环境或用户主目录中发现的配置。`betterleaks_config` 指向
不存在的文件时同样如此。防护会固定这种“不存在”的状态，所以之后再添加该文件是需要复核的变更，而不是
悄悄生效。常规的 `.betterleaks.toml` 在所维护的默认规则之上扩展：

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
python .github/relkit.pyz audit --push --remote origin   # 行由标准输入提供
python .github/relkit.pyz audit --commit --message .git/COMMIT_EDITMSG
```

工作树范围包括被跟踪的文件，以及未被跟踪但也未被忽略的待发布候选文件。暂存范围读取 Git
索引：`relkit.toml`、Betterleaks 配置及其相对路径的策略文件都必须已入索引。未暂存的策略
改动不属于该结论。Betterleaks 扫描已暂存的改动；策略与 Markdown 检查索引内容。

链接检查保留 Markdown 文件名的确切含义，包括 Unicode、空格、开头的 `#`、作为字面量的
通配符字符，以及文件系统允许的换行符。相对链接仍从原来的父目录解析。普通文件名通过本次
运行拥有的列表传递；含换行符的文件名通过大小受限的参数批次传递，所有批次共用 600 秒的
引擎时限。任意批次出现执行错误都会使审计拒绝通过。

历史范围需要干净的**被跟踪**工作树和完整的本地历史。它检查当前 `HEAD`、本地与远端分支、
标签、Git notes，以及已拉取的 GitHub pull request、GitLab merge request 和 Gerrit change
引用。引用名称、附注标签的消息与打标签者身份，与提交和 blob 一同被检查。客户端生成的合成
检查点引用被刻意排除在此范围之外。未被跟踪但也未被忽略的文件仍可由工作树候选检查覆盖；
它们并不会因此成为 Git 历史的一部分。历史检查拒绝浅克隆、replace 引用与非空的 Git graft，
并在其 Git 与扫描器进程中关闭 replace 语义。

远端已经持有的内容无法靠改写历史收回，而保持发布不可变的托管平台根本不允许改写。因此，
历史范围、运行它的发布流程以及 push 范围，对这部分历史中的[私有所有者策略](#私有所有者策略)
发现只列出、不拒绝：`private-value`、`owner-workflow`、`personal-data`，以及在
`forbid_machine_observations` 关闭时的 `machine-observation`（该公开规则报告同一种类）。
对历史范围，远端是 `[release] remote`，否则为 `origin`；对 push 范围，远端是这次 push 的
目标。它持有其远程跟踪引用 `refs/remotes/<remote>/*`
可达的内容，并按对象判断：它持有的 blob、它的树中出现的路径、它拥有的提交。原样继承这种
blob 的新提交随之被列出。仍保留同一行的新 blob 属于新内容，会被拒绝；引用名称、附注标签、
发布所交付的树以及所有公开规则同样照常拒绝。单独的标签不算已发布：发布流程在 push 之前就
创建标签。远程跟踪引用的可信程度与 push 范围相同；过时的引用只会列出更少。远端是本机路径
（工作副本的克隆把副本的本地分支放在 `origin` 下）、没有远程跟踪引用或未配置时，不认为
任何内容已发布。输出先说明哪些内容算作已发布，然后按路径、`commit message` 或
`commit header` 与种类各列一行，并给出出现次数。

push 范围由[仓库防护](#仓库防护)运行。它读取 Git 写入 `pre-push` 钩子的各行
（`<local ref> <local sha> <remote ref> <remote sha>`）以及 `--remote`（Git 作为钩子第一个
参数给出的目标），只检查这次 push 所发布的内容：其提交、远端引用名称与附注标签对象，
规则与历史范围相同，包括对这些提交运行的密钥扫描器。它不要求工作树干净，也不读取任何
其他引用，因此某个会话未完成的改动或另一个分支上的发现，都不会拒绝一次干净的 push。
更新从远端当前的顶端起算；新引用，或本克隆从未拉取过的顶端，则从该 remote 的远程跟踪
引用尚未包含的内容起算，所以向空 remote 的首次 push 会被完整检查。删除与空输入直接通过。
没有任何被推送提交改动的路径不会被再次读取。`--strict` 与 `--require-overlay` 不适用。
push 范围是有意收窄的：只存在于从未推送的本地分支中的私有值，在 push 时不会暴露。
发布流程与 `audit --history` 仍会检查全部历史，因此请在发布前运行它。目标远端已经持有的
blob 中的所有者发现会被[列出](#范围)——无论是被推送的提交原样继承、由合并带入，还是由回退
恢复：这次 push 不会再次发布它。

commit 范围是[提交防护](#仓库防护)在每次提交时运行的检查。它只依据
[私有所有者策略](#私有所有者策略)检查下一次提交新增的内容：暂存 diff 新增的行、它创建或重命名
的路径名、Git 显示为二进制的每个已修改文件的全部内容（包括压缩包），以及在给出 `--message`
时 Git 将记录的提交信息（不含注释行，也不含 `git commit --verbose` 附加的 diff）。当提交只修改
另一行时，文件中已有的取值不会被再次检查。它不读取公开规则、两个引擎、`relkit.toml`、基线和
防护：仍未通过某条公开规则的仓库可以继续提交，这些判定仍由完整审计负责。它的开销取决于改动的
大小而不是仓库的大小，因此足够快，可以放进钩子。没有已配置的策略、也没有 `--owner` 时，它会
通过并提示没有可检查的内容。`--strict` 与 `--require-overlay` 不适用。

在做恢复性复核之前，请先拉取仅存在于托管端的引用：本地检查无法检查克隆中并不存在的对象。
Git 历史扫描及其链接检查都不覆盖托管的 issues、Actions 日志和单独上传的发布文件。这些独立
的发布面参见 [publication review](../publication-review.md)（英文）。

CI 通常使用不带 `--owner` 的 `--history`：公开克隆无从得知私有的所有者取值。在所有者的克隆中，
[已配置的策略](#私有所有者策略)无需参数即适用于所有范围。发布之前使用：

```text
python .github/relkit.pyz audit --history --owner
python .github/relkit.pyz audit --history --owner --require-overlay
```

所有者模式额外要求私有策略和一份有效的已安装 pre-push 防护，并把缺失或无法运行的提交防护
作为警告报告。`--require-overlay` 还要求精确的挂载
链接清单。
[基础的公开策略审计](../../README.zh-CN.md#在既有项目中的第一次检查)并不需要这些参数。

为了离开工作克隆审计历史而建立的一次性克隆不带这些防护，所以在那里运行的所有者审计总会因此
被拒绝。给这样的运行加上 `--sandbox`：pre-push 防护与提交防护不再检查，输出和一条
`guards_not_checked` 警告会说明这一点，而对内容的每一项判定都保持不变，私有策略与 overlay
也包括在内。判定推送所发布内容的 `--push` 以及 `--commit` 会拒绝这个参数。

```text
python .github/relkit.pyz audit --history --owner --sandbox
```

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
问题，都不能通过基线或结构性排除来接受。远端已持有的历史中的所有者发现会被[列出](#范围)，
而不是被接受。

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

面向所有者与 overlay 的 CLI 命令、已安装的所有者防护以及协调器的所有者审计，都使用同一套
选择顺序。

当第一个或第二个来源指定了根目录时，所有范围的 `audit`、`exposure` 和发布协调器无需
`--owner` 就会应用其中的规则：需要调用方记得去要求的检查，正是他们会忘记的那一项。指定的根目录
不存在或无效时，仍然会拒绝。同级目录约定只是一种猜测，因此它本身从不开启策略；`--owner` 仍会
沿用它。`--owner` 额外要求已安装的 pre-push 防护并校验 overlay，因此在防护等待复核后的刷新时，
所有者克隆中的普通审计不会被拒绝。

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

已安装的防护位于 `<git-common-dir>/hooks/pre-push`，运行门禁的 [push 范围](#范围)，即
`audit --push --owner`，并强制使用私有所有者策略。
基础 CLI 安装并不需要它。它会固定被跟踪的投影副本、`relkit.toml`、所配置的密钥策略，以及
——对于基于 workflow 的发布配置——发布用的 workflow。已提交的固定策略输入的修改会由下一次 `relkit update` 重新固定；对已固定输入的其他复核性改动需要显式的
[防护刷新](updates.md#已被修改的文件与防护漂移)。防护需要 0.33.0 或更新版本的投影副本，`protect install`
会拒绝更旧的副本并给出修复方法（先运行 `relkit update`）。

如果 `core.hooksPath` 把 Git 重定向到别处，那位外部所有者必须先提供一个带
`# git-common-dir-hook-dispatcher: pre-push v1` 标记的可执行调度器。release-kit 会先验证
兼容性再写入自己的仓库防护；它从不创建或替换外部调度器。防护会解析 `python`、`python3` 或
`py`。较旧但仍受支持的防护模板会继续执行其固定项，直到被显式刷新；push 范围出现之前的防护会在
每次 push 时检查全部历史，`protect check` 会指明用 `relkit protect install` 换用当前版本。
防护运行的是仓库固定的投影副本，因此请先更新投影副本。关于 0.5.0 之前的归属
模型，请遵循[迁移流程](updates.md#迁移防护钩子的所有者)。

`protect install` 还会写入提交防护 `<git-common-dir>/hooks/commit-msg`，它在每次提交时运行
[commit 范围](#范围)，即 `audit --commit --owner`。它在提交尚未生成时就拦下私有值，而不是等到
push 时，那时包含它的历史已经不得不重写。它运行安装时取得的投影副本拷贝
`<git-common-dir>/hooks/relkit-commit-guard.pyz`，因此每个分支和工作树都由同一份已复核的代码
检查，包括投影副本早于 commit 范围的分支；它不固定其他任何内容，因为它应用的策略位于仓库之外。
提交通过时它不输出任何内容。合并按它带入所在分支的内容检查。`git cherry-pick`、`git rebase`
与 `git revert --no-edit` 不会运行 `commit-msg`，它们产生的提交由 pre-push 防护检查。`relkit update` 之后请再次运行 `protect install` 以刷新这份拷贝。

提交防护是提前预警；门禁仍是 pre-push 防护。因此当投影副本早于 0.34.0，或 `commit-msg` 是
release-kit 未写入的钩子时，`protect install` 会跳过提交防护并说明原因。若要保留这样的钩子，
请在其中调用 `relkit audit --commit --owner --message "$1"`；`protect check` 会接受这样做的
钩子。若 `core.hooksPath` 重定向了 Git，外部所有者的 `commit-msg` 应声明
`# git-common-dir-hook-dispatcher: commit-msg v1`。缺失、过时或无法触达的提交防护是
`protect check` 与 `audit --owner` 的警告，而不是拒绝。`git commit --no-verify` 会跳过它，正如
它跳过所有提交钩子；push 仍会检查该提交。

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
器，包括钩子所使用的解释器。zipapp 中不包含 PyYAML：这条路径请在共享清单中使用 JSON。不要用未经复核
的全局安装来掩盖该错误。任何清单都不会被自动改写或重命名。可选的
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

审计的每个阶段（`policy-rules`、`history`、`overlay`、`provision`、`snapshot`、`betterleaks`、`lychee`）
都会向 stderr 写入带时间戳的 `start` 与 `done (X.Xs)` 行，阶段运行期间至少每 30 秒输出一次
心跳，便于事后把耗时归到具体阶段。策略阶段与引擎并行运行，所以它们的耗时相互重叠，加起来
并不等于审计的总耗时；审计报告最后一行先给出经过的时间，再依次列出各阶段，最后列出同时运行的
阶段：`relkit audit: 14.1s elapsed; provision 0.5s, snapshot 3.9s, betterleaks 0.7s,
lychee 0.6s; at the same time: policy-rules 7.7s`。每条命令还会以
`relkit <command>: <status> (exit N, X.Xs)` 结束：它是 stdout 的最后一行；使用 `--json` 时
写入 stderr，stdout 始终恰好是一个 JSON 对象。参见
[契约](../cli-json.md#summary-line-and-stage-lines)（英文）。
