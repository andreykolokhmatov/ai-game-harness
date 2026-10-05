# AI Game Development Harness

Я хочу разработать локальный open-source AI Game Development Harness.

Главная идея:

> Пользователь даёт короткую идею игры → Harness самостоятельно запускает последовательность AI-агентов → агенты проектируют, создают, тестируют и исправляют Godot-игру → в конце система выдаёт максимально готовый к публикации проект.

Основная специализация:

**Godot → Yandex Games**

Но архитектура должна быть достаточно модульной, чтобы в будущем поддерживать другие движки и платформы.

---

# 1. Твоя роль

Выступай как **senior AI systems architect + senior Python engineer + agentic coding systems engineer**.

Не воспринимай это описание как окончательную архитектуру.

Перед реализацией:

1. Проанализируй идею.
2. Найди слабые места.
3. Изучи существующие open-source решения.
4. Сравни их архитектуры.
5. Предложи улучшенную архитектуру нашего Harness.
6. Объясни важные trade-offs.
7. После этого начинай реализацию.

Не нужно слепо следовать моим предложениям.

Если ты видишь более правильное решение — используй его и объясни почему.

Не надо спрашивать меня о каждой мелочи, если решение очевидно.

---

# 2. Изучи существующие проекты

Перед проектированием внимательно изучи исходный код и документацию наиболее релевантных проектов.

Особенно:

## Godogen

https://github.com/htdt/godogen

Изучи:

* autonomous Godot game development;
* planning;
* coding;
* asset generation;
* runtime feedback;
* Claude Code/Codex integration;
* структуру проекта;
* агентный цикл.

## GameForge-Harness

https://github.com/AlbusChen/GameForge-Harness

Особенно интересуют:

* Solver;
* Harness;
* Evaluator;
* project isolation;
* lifecycle;
* runtime execution;
* screenshots/video/replay;
* independent evaluation;
* Git/state.

## godot-gamestudio

https://github.com/schmoenraad/godot-gamestudio

Изучи:

* maker;
* reviewer;
* revision;
* QA;
* evidence-gated milestones;
* revision limits;
* resumable state.

Также изучи другие актуальные проекты вокруг:

* AI game development;
* Claude Code game development;
* Godot agents;
* multi-agent coding;
* game QA/evaluation.

Не копируй проекты.

Используй их как reference architecture.

Если какое-то решение уже хорошо реализовано в существующем проекте, не изобретай его заново без причины.

---

# 3. Наша основная архитектурная идея

Предварительно я вижу систему так:

```text
USER IDEA
    ↓
ORCHESTRATOR
    ↓
ANALYST
    ↓
ARCHITECT
    ↓
ENGINEER
    ↓
SKEPTIC
    ↓
FIX
    ↓
QA
    ↓
YANDEX AUDITOR
    ↓
STORE / RELEASE
    ↓
FINAL AUDITOR
    ↓
READY
```

Но это только первоначальная идея.

После анализа существующих проектов предложи свою оптимальную state machine.

Возможно, нужны дополнительные состояния:

* planning;
* prototype;
* vertical slice;
* implementation;
* runtime testing;
* regression testing;
* release candidate;
* human approval;
* rollback;
* recovery.

---

# 4. Главная идея Harness

Harness — это **оркестратор**, а не сам AI-разработчик.

Архитектура должна быть примерно:

```text
Python Harness
      ↓
Orchestrator
      ↓
Claude Code Runner
      ↓
Claude Code
      ↓
Godot project
```

Claude Code выполняет работу:

* читает проект;
* создаёт файлы;
* изменяет код;
* запускает shell commands;
* запускает Godot;
* запускает тесты;
* исправляет ошибки;
* работает с Git.

Python Harness отвечает за:

* orchestration;
* state;
* roles;
* prompts;
* model routing;
* запуск Claude Code;
* timeouts;
* retries;
* checkpoints;
* rollback;
* QA;
* evaluator;
* project isolation;
* logs;
* budgets;
* recovery.

---

# 5. Claude Code

Claude Code должен запускаться как отдельный процесс.

Harness должен иметь абстракцию примерно:

```text
ClaudeCodeRunner
```

которая умеет:

* запускать Claude Code;
* передавать prompt;
* выбирать model;
* выбирать effort;
* задавать cwd;
* задавать environment;
* ограничивать timeout;
* получать stdout/stderr;
* определять exit status;
* сохранять результат;
* корректно завершать зависшие процессы.

Не привязывай весь Harness к конкретному способу запуска Claude Code.

Сделай adapter/interface, чтобы его можно было заменить или расширить.

---

# 6. Agents

Предварительно нужны следующие роли.

## Analyst

Получает:

> пользовательскую идею игры.

Создаёт:

* GDD;
* game loop;
* core mechanics;
* target platform;
* controls;
* monetization;
* technical requirements;
* acceptance criteria;
* asset requirements;
* ограничения.

---

## Architect

Создаёт техническую архитектуру:

* Godot version;
* project structure;
* scenes;
* scripts;
* autoloads;
* data structures;
* save system;
* UI;
* input;
* mobile controls;
* resolution/scaling;
* performance;
* audio;
* ads;
* analytics;
* Yandex SDK;
* build/export;
* asset structure.

Определяет порядок разработки.

---

## Engineer

Основной исполнитель.

Работает непосредственно с Godot project.

Может:

* создавать файлы;
* редактировать код;
* создавать scenes;
* писать GDScript;
* запускать Godot;
* запускать тесты;
* читать логи;
* исправлять ошибки;
* работать с Git.

---

## Skeptic

Независимый критик.

Его задача — **пытаться сломать игру**, а не соглашаться с Engineer.

Проверяет:

* gameplay;
* edge cases;
* crashes;
* softlocks;
* broken UI;
* controls;
* save/load;
* performance;
* missing assets;
* broken scenes;
* logic;
* mobile issues;
* SDK;
* monetization;
* unexpected states.

Результат должен быть структурированным.

Например:

```json
{
  "status": "FAIL",
  "issues": [
    {
      "severity": "critical",
      "description": "...",
      "reproduction": "...",
      "expected": "...",
      "actual": "...",
      "suggested_fix": "..."
    }
  ]
}
```

---

# 7. QA

Не полагаться только на LLM.

Где возможно использовать реальные инструменты:

* Godot CLI;
* Python;
* Playwright;
* static checks;
* filesystem validation;
* build checks;
* automated gameplay tests;
* screenshots;
* logs.

LLM должен анализировать результаты, а не быть единственным источником истины.

---

# 8. Independent Evaluator

Очень важный принцип.

Agent, который сделал работу, не должен единолично решать, что работа хорошая.

Поэтому должен существовать независимый evaluation layer.

Пример:

```text
Engineer
    ↓
Game
    ↓
Evaluator
    ↓
PASS / FAIL
```

Evaluator должен иметь доступ к:

* project;
* runtime;
* logs;
* screenshots;
* tests;
* requirements.

Но не должен автоматически исправлять найденные проблемы.

Исправление выполняется отдельным Engineer iteration.

---

# 9. Yandex Games Layer

Это наша главная специализация.

Не зашивай требования Yandex Games в огромные prompts.

Создай отдельный слой:

```text
yandex/
    requirements/
    validators/
    sdk/
    release/
```

Requirements должны быть machine-readable.

Например:

```yaml
- id: yandex_sdk_initialized
  severity: critical
  validation: automatic

- id: mobile_playable
  severity: critical
  validation: automated_or_manual

- id: screenshots_present
  severity: release
  validation: automatic
```

Но **не придумывай требования Yandex Games**.

Перед реализацией этого слоя изучи актуальную официальную документацию Yandex Games и используй её как источник требований.

Каждое требование должно иметь источник.

Yandex layer должен проверять, насколько возможно:

* SDK;
* initialization;
* ads;
* platform integration;
* mobile compatibility;
* build;
* metadata;
* screenshots;
* icon;
* description;
* controls;
* release package;
* актуальные требования платформы.

---

# 10. Store / Release Agent

После завершения gameplay отдельный агент подготавливает release information:

* название;
* short description;
* full description;
* genre;
* tags;
* controls;
* screenshots;
* icon;
* preview;
* monetization information;
* metadata.

Он не должен без причины изменять gameplay.

---

# 11. Final Auditor

Последний этап.

Final Auditor НЕ исправляет проект.

Он получает:

* GDD;
* architecture;
* Git state;
* QA results;
* Skeptic reports;
* Yandex audit;
* release metadata.

И выдаёт:

```text
READY
```

или

```text
FAILED
```

с конкретными причинами.

---

# 12. Git

Каждый project должен находиться под Git.

Например:

```text
projects/
    game_001/
        .git/
        project.godot
        ...
```

Harness должен создавать checkpoints:

```text
architecture-complete
prototype-complete
core-gameplay-complete
qa-pass
release-candidate
```

Если агент ломает проект:

```text
rollback
```

должен быть возможен.

---

# 13. Project isolation

Каждая игра полностью изолирована:

```text
projects/
    game_001/
    game_002/
    game_003/
```

Agent `game_001` не должен случайно изменять `game_002`.

Продумай:

* working directory;
* filesystem restrictions;
* process isolation;
* environment variables;
* secrets;
* cleanup.

---

# 14. Project state

Нужен persistent state.

Предварительно:

```text
.state/
    project.json
    tasks.json
    findings.json
    checkpoints.json
    decisions.json
```

Но предложи лучшую структуру, если она нужна.

State должен позволять:

* продолжить после остановки;
* восстановиться после crash;
* узнать текущий этап;
* узнать предыдущие результаты;
* узнать количество iterations;
* выполнить rollback.

---

# 15. Agent prompts

Не хранить все prompts внутри Python.

Например:

```text
agents/
    analyst.md
    architect.md
    engineer.md
    skeptic.md
    qa.md
    yandex_auditor.md
    release.md
    final_auditor.md
```

Python Harness должен загружать соответствующий role prompt.

Таким образом роли можно изменять без изменения Python-кода.

---

# 16. Model Router

Model и effort должны быть отдельными параметрами.

Например:

```yaml
agents:

  analyst:
    model: claude-opus-5-5
    effort: high

  architect:
    model: claude-opus-5-5
    effort: high

  engineer:
    model: ...
    effort: medium

  skeptic:
    model: ...
    effort: medium

  complex_debug:
    model: claude-opus-5-5
    effort: high
```

Это только пример.

После изучения текущих возможностей Claude Code и доступных моделей предложи оптимальную стратегию.

Не используй Opus 5.5 для каждой мелкой операции без причины.

Нужна escalation policy:

```text
cheap/routine model
      ↓
problem
      ↓
retry
      ↓
complexity detected
      ↓
Opus 5.5
```

Модель должна быть configurable.

Не хардкодить model IDs по всему коду.

---

# 17. Caveman

Caveman / Claude Code skills/plugins могут использоваться в окружении Claude Code.

Но Harness не должен зависеть от Caveman.

Правильная архитектура:

```text
Harness
   ↓
Claude Code
   ↓
Skills / Plugins
   ↓
Caveman (optional)
```

Если Caveman установлен — Claude Code может использовать его.

Если нет — Harness всё равно должен работать.

---

# 18. Cost control

Harness не должен уходить в бесконечный цикл.

Нужны:

* max iterations;
* max retries;
* token/cost budget;
* timeout;
* escalation;
* stop conditions;
* circuit breaker.

Например:

```text
Engineer
 ↓
Skeptic
 ↓
Fix
 ↓
Skeptic
 ↓
Fix
 ↓
если проблема не решена после N iterations
 ↓
Opus escalation
 ↓
если всё ещё FAIL
 ↓
FAILED
```

---

# 19. CLI

Предварительный интерфейс:

```bash
harness create "2D platformer about a robot"

harness run game_001

harness status game_001

harness audit game_001

harness test game_001

harness release game_001

harness rollback game_001

harness logs game_001
```

Предложи улучшенный CLI, если необходимо.

---

# 20. Logging

Для каждого run нужны логи.

Например:

```text
runs/
    2026-10-01_001/
        orchestrator.log
        analyst.log
        architect.log
        engineer.log
        skeptic.log
        qa.log
        yandex.log
```

Нужно понимать:

* какой agent запускался;
* какой task получил;
* какой model использовался;
* что произошло;
* какие ошибки были;
* какие files изменились;
* почему pipeline перешёл дальше.

Не логировать secrets/API keys.

---

# 21. Recovery

Harness должен переживать:

* Claude Code crash;
* timeout;
* Godot crash;
* invalid output;
* malformed JSON;
* network failure;
* interrupted process;
* machine restart.

После перезапуска:

```bash
harness resume game_001
```

система должна понимать, где остановилась.

---

# 22. Open-source architecture

Проект планируется публичным GitHub repository.

Поэтому:

* не привязывать пути к моей машине;
* не хранить secrets;
* использовать config;
* `.env.example`;
* нормальный package structure;
* README;
* installation instructions;
* examples;
* LICENSE;
* CONTRIBUTING;
* CHANGELOG.

Но **не нужно сейчас заниматься маркетингом GitHub или красивым README**.

Сначала работающее ядро.

---

# 23. MVP

Не пытайся сразу сделать весь описанный Harness.

Первый MVP должен быть максимально маленьким:

```text
USER IDEA
    ↓
ANALYST
    ↓
ENGINEER
    ↓
RESULT
```

Цель первого MVP:

```bash
harness create "simple 2D platformer"

harness run game_001
```

После запуска Python Harness:

1. создаёт isolated project;
2. запускает Claude Code;
3. передаёт ему роль;
4. Claude Code создаёт/изменяет Godot project;
5. Harness получает результат;
6. сохраняет state;
7. делает Git checkpoint.

После того как это работает:

```text
ANALYST
 ↓
ARCHITECT
 ↓
ENGINEER
 ↓
SKEPTIC
 ↓
FIX
 ↓
QA
```

И только потом:

```text
YANDEX
 ↓
RELEASE
 ↓
FINAL AUDIT
```

---

# 24. Важный принцип разработки

Не создавай огромную систему сразу.

Работай итеративно:

```text
design
 ↓
implement
 ↓
run
 ↓
test
 ↓
fix
 ↓
commit
 ↓
next component
```

После каждого значимого этапа система должна реально запускаться.

Не создавай десятки файлов заранее без проверки.

---

# 25. Первый шаг

Сейчас НЕ начинай писать весь Harness.

Сначала:

1. Изучи Godogen.
2. Изучи GameForge-Harness.
3. Изучи godot-gamestudio.
4. Изучи другие наиболее релевантные проекты.
5. Изучи актуальную документацию Claude Code.
6. Изучи актуальные возможности Claude Opus 5.5 / Sonnet и model routing.
7. Изучи актуальную официальную документацию Yandex Games, но пока только для понимания будущего Yandex layer.

После исследования создай:

```text
ARCHITECTURE.md
```

В нём:

* сравнение существующих решений;
* найденные архитектурные паттерны;
* проблемы;
* proposed architecture;
* state machine;
* agent architecture;
* project structure;
* model routing;
* data flow;
* recovery strategy;
* MVP plan.

После этого предложи структуру репозитория.

**Не начинай реализацию до того, как покажешь мне Architecture proposal.**

Я хочу сначала посмотреть, что ты придумал, и затем дать добро на реализацию.
