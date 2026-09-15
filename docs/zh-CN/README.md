# release-kit 简体中文文档

[英文文档](../../README.md)是规范版本。中文指南覆盖采用该门禁的完整路径：安装、第一次
检查、审计策略与更新。它们与英文文档对应同一个发布版本。

| 任务 | 文档 |
| --- | --- |
| 安装 CLI 并完成第一次检查 | [概览与快速上手](../../README.zh-CN.md) |
| 配置私有路径、基线、所有者规则、仓库防护或 overlay | [审计策略与范围](audit.md) |
| 更新项目固定的 CLI、刷新防护、回滚或恢复 | [更新与恢复](updates.md) |

## 仅有英文版本的文档

以下内容面向集成方与维护者，而不是门禁的安装与日常使用：

| 任务 | 文档 |
| --- | --- |
| 校验并导出经过整理的发布说明 | [Release notes](../notes.md) |
| 本地构建并投递到目录或 GitHub | [Local releases](../local-releases.md) |
| 维护 GitHub Actions 发布流程 | [Actions adapter](../release-coordinator.md) |
| 使用面向智能体的插件 | [Plugin](../plugin.md) |
| 接入其他本地 MCP 客户端 | [Standalone MCP](../mcp.md) |
| 消费机器可读的结果 | [CLI JSON contract](../cli-json.md) |
| 了解平台固定、缓存与信任边界 | [Distribution](../distribution.md) |
| 复核本地审计看不到的发布面 | [Publication review](../publication-review.md) |
| 从源码构建项目 | [CONTRIBUTING.md](../../CONTRIBUTING.md) |
| 查看版本之间的变更 | [CHANGELOG.md](../../CHANGELOG.md) |
| 报告安全漏洞 | [SECURITY.md](../../SECURITY.md) |
| 查看许可证 | [LICENSE](../../LICENSE) |

命令输出、规则名称、配置键与 CLI 消息一律不翻译：翻译之后，这些字符串将无法在文档与源码
中被检索到。
