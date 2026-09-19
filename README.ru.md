# release-kit

[![Релиз](https://img.shields.io/github/v/release/Muratovnik/release-kit?label=release&color=blue)](https://github.com/Muratovnik/release-kit/releases)
[![Лицензия](https://img.shields.io/github/license/Muratovnik/release-kit?color=blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue)](https://www.python.org/downloads/)
[![Загрузки](https://img.shields.io/github/downloads/Muratovnik/release-kit/total?label=downloads)](https://github.com/Muratovnik/release-kit/releases)

[English](README.md) · **Русский** · [简体中文](README.zh-CN.md)

Проверьте репозиторий Git перед публикацией. Одна команда запускает поиск секретов,
проверку ссылок в Markdown и те правила для файлов и истории, которые проект объявил
для себя. Она же готовит релизные файлы, доставляет их в каталог или на GitHub и
проверяет опубликованное.

## Как это выглядит

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

Код `0` означает, что настроенные проверки прошли для этой области. Код `1` означает
находки: прочитайте пути и исправьте или разберите их. Код `2` означает сбой
конфигурации или выполнения — это не чистый результат проверки безопасности. Для
автоматизации используйте `audit --json` и
[контракт результата](docs/cli-json.md) (English).

## Установка

Один инструмент, одна конфигурация на проект — та же схема, что у любого другого
линтера. Выберите форму по тому, кто его запускает.

| Форма | Установка | Откуда берётся версия |
| --- | --- | --- |
| На вашей машине | `uv tool install <URL wheel>` | с вашей машины |
| Закреплена в репозитории | zipapp, закоммиченный в проект | из закоммиченных байтов |
| Dev-группа Python-проекта | `uv add --dev "release-kit @ <URL wheel>"` | из lock-файла этого проекта |

**Для CI и Git-хуков авторитетна проектная форма.** Установка на уровне машины — это
удобство; воспроизводимость клона обеспечивает не она.

### На вашей машине

```text
gh release download --repo Muratovnik/release-kit --pattern 'release_kit-*' --dir .cache/relkit-download
python -c "import glob,hashlib,pathlib,subprocess,sys; w=glob.glob('.cache/relkit-download/*.whl')[0]; e=pathlib.Path(w+'.sha256').read_text().split()[0]; a=hashlib.sha256(pathlib.Path(w).read_bytes()).hexdigest(); sys.exit('checksum mismatch') if a!=e else subprocess.run(['uv','tool','install',w],check=True)"
```

`gh release download` без тега берёт последний релиз, поэтому ни одна из команд не
называет версию. Вторая сверяет файл с опубликованной контрольной суммой, прежде чем
передать его `uv`; вместо `uv` так же работает `pipx`.

Чтобы установить без `gh`, назовите релиз в URL:

```text
uv tool install https://github.com/Muratovnik/release-kit/releases/download/v0.27.1/release_kit-0.27.1-py3-none-any.whl
```

Эта единственная команда не может сказать `latest`: имя файла wheel обязано нести свою
версию, а `releases/latest/download/` отдаёт только активы самого нового релиза. Она
также пропускает проверку контрольной суммы. Предпочитайте две команды выше, если вы
не закрепляете версию намеренно.

Контрольная сумма, опубликованная рядом с файлом, обнаруживает повреждение; это не
независимая аутентификация издателя. Где это доступно, проверяйте неизменяемый релиз
через `gh release verify` и `gh release verify-asset`. См.
[границы доверия](docs/distribution.md#integrity-and-trust) (English).

### Закреплена в репозитории

Zipapp нужен только Python в PATH, поэтому он подходит любому репозиторию, в том числе
без всякой упаковки Python. Его имя файла никогда не меняется, так что его можно взять
прямо из последнего релиза, проверить и положить на место одной командой, работающей в
любой оболочке:

```text
curl -fLO --output-dir .cache/relkit-download --create-dirs https://github.com/Muratovnik/release-kit/releases/latest/download/relkit.pyz
curl -fLO --output-dir .cache/relkit-download https://github.com/Muratovnik/release-kit/releases/latest/download/relkit.pyz.sha256
python -c "import hashlib,pathlib,shutil,sys; s=pathlib.Path('.cache/relkit-download'); t=pathlib.Path('.github/relkit.pyz'); e=(s/'relkit.pyz.sha256').read_text().split()[0]; a=hashlib.sha256((s/'relkit.pyz').read_bytes()).hexdigest(); sys.exit('checksum mismatch') if a!=e else None; sys.exit('already installed: use the update guide') if t.exists() else None; t.parent.mkdir(parents=True,exist_ok=True); shutil.copy(s/'relkit.pyz',t)"
```

Оба файла должны происходить из одного релиза, что `latest` и обеспечивает — пока между
двумя загрузками не будет опубликован новый релиз. `gh release download --pattern
'relkit.pyz*'` забирает их одним запросом, если вы предпочитаете не зависеть от этого.

Запускайте как `python .github/relkit.pyz audit`. Закоммитьте файл; каждый проект ведёт
собственную копию, и обновление одной не обновляет остальные. Последующие изменения
проходят через [руководство по обновлению](docs/ru/updates.md), а не через перезапись.

## Первая проверка в существующем проекте

Работайте из корня того репозитория Git, который хотите проверить. Ни хук, ни приватный
соседний каталог, ни MCP-клиент, ни учётная запись хостинга для этого пути не нужны.

Создайте `relkit.toml`, осознанно выполнив слияние, если файл уже существует:

```toml
[exposure]
betterleaks_config = ".betterleaks.toml"
required_ignores = [".cache"]
```

Создайте `.betterleaks.toml`:

```toml
title = "project secret scanning"
betterleaksMinVersion = "1.8.1"

[extend]
useDefault = true
```

Добавьте `.cache/` в `.gitignore` проекта **до запуска аудита**, сохранив существующие
правила, и создайте каталог, чтобы первый аудит проверил игнорирование только каталога
раньше, чем в него запишет любой сканер:

```text
python -c "from pathlib import Path; Path('.cache').mkdir(exist_ok=True)"
```

Ровно эти стартовые файлы лежат и в [examples/audit](examples/audit/README.md) (English).
Ни один сканер в этом примере не отключён. Затем:

```text
relkit audit
```

Первый аудит может скачать закреплённые архивы сканеров. Он проверяет отслеживаемые
файлы и неотслеживаемые, но не игнорируемые кандидаты на публикацию, и не индексирует,
не коммитит, не устанавливает хук, не создаёт тег, не выполняет push и не публикует.
Загрузки и проверенные сканеры живут в `.cache`, но никогда в Git.

Закоммитив конфигурацию, проверьте всю локальную историю:

```text
relkit audit --history
```

Истории нужен неусечённый клон и чистое дерево отслеживаемых файлов; она не может
исследовать ссылки, которые никогда не забирались. Существующие исторические находки
могут потребовать отдельного ответа, согласованного с владельцем; не переписывайте
историю только ради прохождения проверки.

## Требования и ограничения

| Применение | Требования |
| --- | --- |
| CLI | Python 3.11+ и Git в PATH |
| Установка на машину | `uv` или `pipx`; у самого wheel нет зависимостей времени выполнения |
| Полный аудит | Закреплённые исполняемые файлы Betterleaks и Lychee; первый аудит скачивает и проверяет их |
| Платформы полного аудита | Linux x64/arm64 (сборки GNU), macOS x64/arm64, Windows x64 |
| Доставка на GitHub или обновления | GitHub CLI; доставке нужны `gh` 2.98.0+, аутентификация и описанные права в репозитории |
| Плагин | Python 3.11+, `uv`, каталог установки с правом записи и локальный клиент, поддерживающий stdio-конфигурацию этого плагина |

У Python CLI и внешних сканеров разные требования к платформе. Закреплённого нативного
актива Lychee для Windows arm64 не существует. Неизвестные или незакреплённые платформы
сканеров получают отказ, а не непроверенный бинарный файл. См.
[детали дистрибуции и платформ](docs/distribution.md) (English).

Аудит не является общей оценкой безопасности и не доказывает, что проект безопасно
публиковать. Приватные имена объявляет их владелец. Встроенные текстовые правила
используют конкретные шаблоны, а не понимание языка в общем виде. Аудит не смотрит в
issues GitHub, журналы Actions и незабранную историю. Указатели LFS, подмодули и
неподдерживаемые сжатые архивы проваливают соответствующую проверку. Связанные рабочие
деревья не поддерживаются программой обновления, координатором релизов и адаптером MCP.
Прочитайте [области и исключения](docs/ru/audit.md#области), прежде чем принимать гейт.

## Выберите следующий шаг

| Задача | Руководство |
| --- | --- |
| Настроить приватные пути, базовые линии, правила владельца или overlay | [Политика и области аудита](docs/ru/audit.md) |
| Обновить закреплённый CLI проекта, обновить его защиту или откатиться | [Обновления и восстановление](docs/ru/updates.md) |
| Проверить и выгрузить курируемые заметки к релизу | [Release notes](docs/notes.md) (English) |
| Собрать локально и доставить в каталог или на GitHub | [Local releases](docs/local-releases.md) (English) |
| Поддерживать публикацию через GitHub Actions | [Actions adapter](docs/release-coordinator.md) (English) |
| Использовать плагин для агентов | [Install and use the plugin](docs/plugin.md) (English) |
| Интегрировать другой локальный MCP-клиент | [Standalone MCP](docs/mcp.md) (English) |
| Потреблять машиночитаемые результаты | [CLI JSON contract](docs/cli-json.md) (English) |

Полный указатель русских документов — в [docs/ru/README.md](docs/ru/README.md).

## Устранение неполадок

| Симптом | Что проверить дальше |
| --- | --- |
| `No module`, неподдерживаемый Python или отсутствующий `python` | Проверьте `python --version`; используйте нужный интерпретатор, а не устаревшее окружение какого-нибудь приложения |
| Ошибка загрузки сканера или кэша | Проверьте доступ к сети и игнорирование `.cache/`; `--no-download` работает только с полными проверенными кэшами |
| Нет `relkit.toml` или политики секретов | Запускайте из нужного корня проекта; проверьте оба файла из стартового примера |
| История отказывает из-за локальных изменений | Пока правите, используйте `audit` или `audit --staged`; коммитьте проверенные изменения перед `--history` |
| Машинная установка и проектное закрепление расходятся | Для CI и хуков предпочитайте проектную форму; сравните `relkit --version` с закреплённой копией |
| Дрейф существующей проекции или защиты | Используйте [руководство по обновлению](docs/ru/updates.md), а не перезапись или `--no-verify` |
| Плагин не обнаруживается или запускает старую версию | Следуйте [plugin troubleshooting](docs/plugin.md#troubleshooting) (English), включая перезагрузку нативного клиента |

## Участие и сообщения о проблемах

[CONTRIBUTING.md](CONTRIBUTING.md) (English) описывает изолированную настройку
разработки, базовую проверку и полную проверку дистрибуции. Сопровождающие используют
[distribution guide](docs/distribution.md) и
[publication review](docs/publication-review.md) (English), прежде чем сделать
что-либо существенное публичным. Об уязвимостях сообщайте приватно, как описано в
[SECURITY.md](SECURITY.md) (English).

Лицензия MIT, см. [LICENSE](LICENSE). Поддерживаемые движки сохраняют собственные
лицензии и берутся из их официальных релизов, а не копируются в CLI.
