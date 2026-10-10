# AI Game Development Harness

Короткая идея игры превращается в готовую браузерную игру на Godot 4. Harness (Python) управляет цепочкой агентов Claude Code и сам проверяет результат инструментами: Godot, сценарии, Chromium.

```text
идея ─▶ Planner ─▶ план (GDD, контракт, вехи, критерии)
        веха: Engineer ─▶ VERIFY (Godot, сценарии, скриншоты, браузер) ─▶ Evaluator ─┬─ PASS ─▶ следующая веха
                  ▲                                                                  └─ FAIL ─▶ исправление (с эскалацией)
                  └──────────────────────────────────────────────────────────────────────────┘
после m1: решение человека (approve / revise / stop) ─▶ остальные вехи ─▶ release ─▶ Final gate ─▶ READY
```

Подробности и все решения: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md). Лицензия: Harness нельзя продавать, игры, сделанные с ним, можно (раздел 8.1).

## Что нужно

- Linux (основная платформа) или Windows
- Python 3.11+ и [uv](https://docs.astral.sh/uv/)
- [Claude Code](https://claude.com/claude-code) с входом по подписке (`claude` в PATH)
- Godot 4.4.x и его export templates
- Linux: `xvfb-run` (скриншоты без окон), для web-проверок Playwright с Chromium

```bash
uv sync --extra web
uv run playwright install chromium
uv run harness doctor          # всё ли на месте
```

Пути к Godot и прочее, что зависит от машины, кладутся в `config/local.yaml` (не в Git). Модели заданы только в `config/models.yaml`, лимиты и гейты в `config/harness.yaml`.

## Как сделать игру

```bash
uv run harness create "casual 2D: catch falling fruit with a basket"   # -> game_001
uv run harness run game_001 --wait     # план, первая веха, проверки; --wait переживает лимит подписки
uv run harness status game_001
```

Игра лежит в `workspace/game_001/repo` (обычный Godot-проект, его можно открыть в редакторе), отчёты проверок в `workspace/game_001/harness/reports/<commit>/report.md`.

После первой вехи (прототипа) проект ждёт решения человека:

```bash
uv run harness approve game_001                      # принять и делать следующие вехи
uv run harness revise game_001 "кнопки мелкие на телефоне"   # вернуть инженеру с комментарием
uv run harness stop game_001                         # закончить
uv run harness run game_001 --wait                   # продолжить после approve или revise
```

Когда все вехи приняты:

```bash
uv run harness release game_001    # zip с index.html, иконка, обложка, скриншоты, тексты EN/RU, Final gate
```

## Остальные команды

| Команда | Что делает |
|---|---|
| `harness test game_001` | только проверки, без агентов, на текущем коммите игры |
| `harness run game_001 --now` | продолжить паузу по лимиту сразу, не дожидаясь записанного времени сброса |
| `harness rollback game_001 --to cp/prototype` | вернуть игру к чекпоинту (`cp/plan`, `cp/prototype`, `cp/m2`, ...); выход из BLOCKED |
| `harness logs game_001` | журнал событий проекта |

## Как устроено

- `harness/orchestrator/` состояния, вехи, Planner, Evaluator, эскалация
- `harness/verify/` все проверки: Godot, сценарии, контракт, ассеты, языки, браузер
- `harness/runners/` запуск Claude Code (и mock для тестов), права по ролям
- `agents/` промпты ролей и задач
- `templates/godot/` шаблон игры: автозагрузки `Platform` и `Game`, Web-пресет, локализация
- Состояние проекта: append-only журнал `workspace/<игра>/harness/events.jsonl`; `workspace/` в Git не попадает

Тесты Harness: `uv run pytest` (тесты с Godot и Chromium пропускаются, если их нет).
