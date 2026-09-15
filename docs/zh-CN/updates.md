# 项目更新与恢复

[英文版](../updates.md)是规范版本。每个接入项目各自跟踪自己的 `.github/relkit.pyz`。更新
源仓库、Python 包、插件或某一个接入项目，都不会更新其他项目。首次安装请使用
[快速上手](../../README.zh-CN.md#在既有项目中的第一次检查)。

## 更新一个项目

在包含 0.6.0 或更新版本投影副本的项目中：

```text
python .github/relkit.pyz update --dry-run
python .github/relkit.pyz update
```

如果已安装 Python 包，等价命令是 `relkit update`。两者都只更新本项目的投影副本，而不会更新
全局 Python 包、源码检出、其他项目、策略或 CI。过程中不会创建任何别名或全局安装。
[插件](../plugin.md)（英文）可以改为用 `relkit_sync` 把某个项目显式同步到它自带的 CLI；
仅仅安装插件不会改变任何项目。

默认来源是分发构建器记录下来的 GitHub 仓库。当没有记录来源，或需要显式选择另一个受信任的
发布者时，使用 `--repository OWNER/REPO`。`--release v0.6.0` 选择某个特定的稳定发布版本；
否则使用最新的非预发布版本。分支或标签不是更新来源：发布者必须把 `relkit.pyz` 作为构件附加
到已发布的版本上。更新程序会拒绝降级，也会拒绝声称是已安装版本但字节不同的构件。

GitHub 传输使用可选的 `gh` CLI 及其既有认证，包括对私有仓库的访问。认证与发布下载由原生
CLI 负责；release-kit 不保存令牌，也不引入新的认证流程。
[发布元数据](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)、
[构件摘要](https://docs.github.com/en/rest/releases/assets)与
[下载结果](https://cli.github.com/manual/gh_release_download)在大小、摘要、内嵌仓库与版本
上必须一致。缺失摘要会被拒绝。这是针对受信任发布者的完整性校验，而不是独立的发布者签名。
发布协调器的签名发布校验是另一项独立操作。

对于离线或本地构建的候选构件，请提供一个独立复核过的摘要：

```text
python .github/relkit.pyz update --artifact /path/to/trusted/relkit.pyz --sha256 DIGEST --dry-run
python .github/relkit.pyz update --artifact /path/to/trusted/relkit.pyz --sha256 DIGEST
```

从本地文件更新不需要 `gh`。写入之前，命令会显示投影副本与既有自有防护的新旧版本、路径和
SHA-256 取值。常规更新要求干净的检出、被跟踪的投影副本与配置、普通的仓库内 Git 元数据，
以及在已安装的情况下有效的既有防护。它们不会自动 stash。链接工作树、走别名的更新路径、
通过环境变量覆盖 Git 目录或索引、任意钩子以及不兼容的外部调度器都会被拒绝。0.5.0 之前的
受保护安装需要先完成[防护所有者迁移](#迁移防护钩子的所有者)。

交互式使用会要求确认。非交互式使用会被拒绝，除非在确切的变更内容获得批准之后提供 `--yes`。
项目自身规定所需的单独钩子许可依然是必需的。该参数表示确认，它并不替代授权。

`--dry-run` 只做检查与下载，绝不执行候选构件、保存备份或改动投影副本与防护。一个仓库级的
临时锁会串行化多次运行。若需要结构化预览与漂移拒绝，请使用 `--json`，随后以
`--yes --plan-hash HASH` 提交同一个请求；参见
[reviewed plans](../cli-json.md#reviewed-update-plans)（英文）。

确认之后，更新程序会检查候选构件的运行时版本，把原始文件及其权限位保存到
`.git/relkit-update-<id>/`，记录 `.git/relkit-update.json`，并用新的投影副本运行一次工作树
审计。它只会刷新一份已经安装且完整的仓库防护，并加以校验。外部调度器只被检查、不被修改；
不会安装新的防护。`--no-download` 同时也会关闭审计引擎的下载。审计失败会恢复旧的构件与
防护。若并发改动使恢复变得不安全，这些改动会被保留，并在报告中给出备份位置以便恢复。

复核投影副本的 diff，运行项目测试，提交，然后运行基于干净历史的发布审计——在适用处加上
`--owner`。更新不会暂存、提交、推送、发布、改动引用、放宽策略、启用可选规则或修改 workflow。
它们的工作树检查不是所有者最终的发布结论。

## 已被修改的文件与防护漂移

如果投影副本或配置被手工改动过，`protect check` 会逐项列出发生变化的输入，并给出固定值与
当前的 SHA-256。请先复核这些改动、取得所需的钩子许可，然后再执行：

```text
python .github/relkit.pyz update --refresh-guard --dry-run
python .github/relkit.pyz update --refresh-guard
python .github/relkit.pyz protect check
```

该模式允许工作树不干净，会检查当前的构件与策略，备份旧防护，并且只改动受支持防护的信任
固定项。它不会下载发布版本，也不会修改投影副本与配置。常规更新会因既有漂移而拒绝执行，
而不会把一次无关的更新当作信任该漂移的许可。自定义或被修改过的钩子模板需要所有者人工复核。

同一模式也用于接纳由更早版本 release-kit 写入的防护。一个固定了当前输入的旧模板不算漂移，
并且仍在持续生效：`protect check` 会如实报告，刷新操作会在备份、校验与可回滚的前提下重写
它。这不是需要绕过流程的紧急情况。

## 首次更新与回滚

更旧的内嵌版本没有 update 命令。请先构建或获取一份受信任的 0.6.0+ zipapp，用它来更新那份更
旧的、被跟踪的副本：

```text
python /path/to/trusted/relkit.pyz update --root . --artifact /path/to/trusted/relkit.pyz --sha256 DIGEST
```

当项目中更旧的 CLI 缺少修复能力时，外部 zipapp 同样可以运行 `update --root . --refresh-guard`。
此后的更新使用项目自己的命令；构建 release-kit 并不会迁移任何项目。

恢复最近一次事务（包括仅刷新防护的那种）：

```text
python .github/relkit.pyz update --rollback
```

如果被恢复的版本早于该命令出现的时间，请继续保留那份受信任的外部更新程序。回滚会校验备份，
并在受防护的输入或钩子此后被改动时拒绝执行。它不会重置 Git，也不会丢弃用户的改动。发生崩溃
后，请查看 `.git/relkit-update.lock` 中的 PID，在证明该进程确已停止之后，只删除这一个锁文件。
存在未完成的回执时，必须先回滚才能再次更新。退出码 `0` 表示成功、无操作或 dry-run；退出码
`2` 表示拒绝或运行失败，但并不证明没有产生任何影响。

## 备份保留

一份回执对应一份备份；更早的备份无法通过 `--rollback` 恢复。一次完成的更新会报告还剩多少
备份。`update --prune-backups` 恰好删除那些已被取代的备份，绝不会删除当前回执对应的备份，
也绝不会删除包含任何非更新程序自有文件的目录。批准之前请先用 `--dry-run` 预览。除此之外，
备份属于需要保留的恢复数据，而不是普通的一次性缓存。

## 迁移防护钩子的所有者

本节适用于跨越 0.5.0 归属边界迁移的 **0.3.x/0.4.x** 受保护安装，而不适用于当前 CLI 的首次
安装。更旧的安装程序可以创建或替换重定向调度器；0.5.0 及以后版本一律视其为外部所有。

请先保存既有的投影副本、公开策略、仓库防护与重定向调度器的字节内容。请外部钩子所有者安装一
个带 `# git-common-dir-hook-dispatcher: pre-push v1` 标记的可执行调度器，并确认它确实会调用
Git 公共目录下的钩子。把同一份已复核的 0.5.0 或更新版本的构件与策略投影到每一个受影响的接入
项目。在接受这次迁移之前，先运行 `protect install`、`protect check`，并在每个干净的接入项目
中运行 `audit --history --owner`。

迁移调度器之后，绝不要再用 0.5.0 之前的安装程序去刷新防护。回滚会把保存下来的构件、策略、
仓库防护与调度器作为一个整体一致地恢复；混用两套归属契约可能会选中错误的钩子。任何迁移步骤
都不授予 release-kit 修改由他人拥有的钩子的权限。
