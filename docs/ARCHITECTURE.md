# AI Game Development Harness: архитектура

Статус: **черновик на согласование** (этап 1 плана). Код Harness не пишется, пока документ не одобрен.
Дата исследования: 2026-10-05.

Документ опирается на `docs/original_brief.md` и `docs/PLAN_v2.md`. Где я предлагаю отойти от PLAN_v2, это отмечено явно и объяснено (раздел 3).

---

## 0. Коротко

1. **Тонкий оркестратор, толстая проверка.** Все три референсных проекта и опыт Anthropic по long-running harness сходятся в одном: дробные цепочки ролей и микро-задач со временем убирают, потому что модели сами хорошо декомпозируют работу. Не убирают **независимую проверку результата инструментами**. Поэтому в нашем Harness мало ролей и крупные шаги (вехи, а не задачи), но верификация (Godot, сценарии, скриншоты, Web, Yandex-валидаторы) спроектирована как ядро.
2. **Godot 4.7.x + GDScript + Compatibility-рендерер + однопоточный Web-экспорт.** C# в Godot 4 не экспортируется в Web, так что Godogen-подход «C# вместо GDScript» нам недоступен. Компенсируем строгой типизацией, `--check-only` и руководством по ловушкам GDScript.
3. **Контракт игры (Game Contract).** Planner фиксирует имена input-действий, ключи состояния и debug-команды. Engineer их реализует, Evaluator тестирует игру через них, не завязываясь на имена нод. Это делает приёмочные сценарии независимыми от реализации.
4. **Платформенный слой с первого дня.** Автозагрузка `Platform` с бэкендами `mock` и `yandex`, пауза звука при потере фокуса и рекламе, сохранения через сервис. Yandex-аудит остаётся в конце, но архитектура под него закладывается в шаблоне.
5. **ClaudeCodeRunner: CLI-адаптер (`claude -p --output-format stream-json`) как основной, Agent SDK как второй адаптер, Mock-адаптер для тестов.**
6. **Модели:** Planner и Evaluator на Opus 5.5, Engineer на Sonnet 5.5, эскалация Sonnet → Opus 5.5 high → (опционально) Fable 5.1. Haiku 4.5 не используем: его снятие возможно уже с 2026-10-15, и он не поддерживает effort.

---

## 1. Что изучено

### 1.1 Референсные проекты (исходники склонированы и прочитаны)

| | **Godogen** (htdt) | **GameForge-Harness** (AlbusChen) | **godot-gamestudio** (schmoenraad) |
|---|---|---|---|
| Суть | Генератор «тонкого рантайма»: `publish.sh` кладёт в новый репозиторий игры `CLAUDE.md`, гайд по движку и skill `asset-gen`. Дальше Claude Code/Codex работает сам | Python-Harness (~28k строк): одноразовый workspace, открытая сессия solver'а, потом **независимый evaluator**, невидимый для solver'а | Набор skills/агентов + Python-скрипт состояния `studio_state.py`; Supervisor ведёт вехи maker → reviewer → QA |
| Оркестрация | Нет пайплайна. В changelog 2026-07-02 явно **удалили** многостадийный planner/decomposer/architecture: «модель планирует сама» | Есть state machine (INIT … PLAN, IMPLEMENT, COMPILE, PLAY_TEST, DIAGNOSE, REPAIR … COMPLETE), но опубликованные результаты (GameCraft-Bench 140/140 сборок, GameDevBench 64%) получены профилем `native-open` с **нулём repair-проходов Harness**: модель работает свободно, Harness только оценивает | Вехи с измеримыми критериями, ≤4 специалистов, ≤2 раунда ревизии, затем блокер |
| Проверка | «Proof over claims»: видео 15–20 с через `--write-movie` + ffmpeg под Xvfb, агент сам смотрит кадры. Внешнего судью (Gemini) удалили 2026-04-26 как «не дающего сигнала» | Гейты: import, runtime-probe (SceneTree-скрипт: грузит main scene, settle-кадры, скриншот, дамп дерева нод в JSON), **replay ввода** (`input-trace.json`), видео, квитанции | QA-доказательства с SHA-256, привязка ревью к ревизии артефакта, «свежесть» доказательств (скриншот удаляется перед прогоном) |
| Состояние | `README.md` в репо игры (статус, остаток, таблица ассетов), переживает compaction | Квитанции, receipts, атрибуция сбоев | `.godot-gamestudio/studio.json` (schema v2, миграции) |
| Изоляция | Нет | Профили `native-open` / `supervised-native` / `strong-isolated` (внешний runner-протокол для VM/контейнера, fail-closed) | Владение путями, без пересечений при параллельной работе |
| Ассеты | Сильная сторона: Gemini/Grok/локальный Qwen-Image, Tripo3D, спрайты из видео, rembg; подтверждение трат у пользователя; манифест с колонкой **in-game size** | Нет | Sprite-studio skill + грейдер спрайт-листов |
| Язык игры | C# (.NET), аргумент: меньше молчаливых ошибок, чем в GDScript | GDScript/C# | Любой |

#### Godogen: что берём
- **Гайд по движку содержит только то, что модель не выведет сама**: ловушки, которые проходят компиляцию и ломаются в рантайме, рецепт запуска и захвата. Кладётся в `CLAUDE.md` репозитория игры.
- **Долговременное состояние для агента в файле внутри репо** (`docs/PROGRESS.md`): переживает compaction и новые сессии.
- **Манифест ассетов с колонкой «размер в игре»** (без неё агенты систематически масштабируют ассеты неправильно), плюс у нас колонки лицензии и источника.
- **Каждый вызов Godot под таймаутом** (на macOS фатальная ошибка вешает процесс навсегда).
- **`--write-movie` + `--fixed-fps` для детерминированного захвата.**
- **Подтверждение платных генераций.**

#### Godogen: что не берём
- C#: не экспортируется в Web на Godot 4 (см. 1.5).
- Полный отказ от внешней оценки. Godogen доверяет самопроверке, но у него нет требований платформы и модерации. У нас есть объективные критерии (Yandex, Web, приёмка), и самооценка модели заведомо смещена (это подтверждает и Anthropic, см. 1.2).

#### GameForge: что берём
- **Принцип «не предписывать модели, как строить, и не давать модели решать, работает ли игра».** Это наша главная граница: Engineer свободен внутри вехи, приёмка от него не зависит.
- **Runtime-probe как SceneTree-скрипт**: грузит main scene, ждёт кадры, снимает скриншот, дампит дерево нод/позиции/видимость в JSON, проигрывает input-trace (`Input.parse_input_event`). Это реализуемо без кода в самой игре.
- **Атрибуция сбоев по классам**: ошибка агента, сбой движка/сборки, инфраструктура хоста, дефект игры, сбой оценщика. Инфраструктурные сбои не тратят итерации исправления.
- **Бюджет repair-петли** по итерациям, времени и деньгам одновременно (`RepairBudget`).
- **Mock-бэкенд модели** для тестов самого Harness без трат.
- **Протокол внешнего изолирующего runner'а** (fail-closed) как модель для будущей Docker-изоляции.

#### GameForge: что не берём
- Масштаб и бенчмарк-инфраструктуру. Нам нужен продукт для одной платформы, а не исследовательский стенд.
- Скрытие приёмочных данных от solver'а целиком. У нас критерии приёмки видимы Engineer'у текстом (иначе он не знает, что строить), а **исполняемые приёмочные сценарии принадлежат Evaluator'у** и лежат вне репозитория игры.

#### godot-gamestudio: что берём
- **Вердикт ревью привязан к ревизии артефакта.** У нас: каждый отчёт Evaluator'а и каждое доказательство привязаны к commit SHA. Если HEAD изменился, вердикт устарел.
- **Свежие доказательства**: перед прогоном удалить ожидаемый скриншот, требовать код 0 и новый файл. Их пилот поймал модель, которая «утверждала, что сделала скриншот», хотя Godot упал по таймауту.
- **`inconclusive` вместо `pass`**, если инструмент недоступен.
- **Лимит раундов ревизии → блокер**, а не бесконечный цикл.
- **Пауза для человека** перед деструктивной реструктуризацией, расширением scope, платными сервисами.
- **`godot_guard.py`**: Godot в отдельной группе процессов, SIGTERM → SIGKILL по таймауту, код 124.

#### godot-gamestudio: что не берём
- 18 специализированных ролей. Для наших игр (простые 2D web-игры) это лишние передачи контекста и стоимость.

### 1.2 Другие релевантные работы

| Источник | Вывод для нас |
|---|---|
| Anthropic, «Effective harnesses for long-running agents» | Initializer один раз создаёт окружение, `progress`-файл, список фич (pass/fail) и первый коммит. Каждая следующая сессия читает прогресс и git log, проверяет, что всё работает, делает одну фичу, тестирует, коммитит. Это прямо ложится на наши вехи |
| Anthropic, «Harness design for long-running apps» | Planner / Generator / Evaluator. Evaluator проверяет приложение через Playwright по критериям с порогами. **Самооценка смещена**: модели хвалят свою работу. По мере роста моделей per-sprint декомпозицию убрали, Evaluator перенесли в конец прогона. Пример «Retro Game Maker»: соло 20 мин / $9, полный harness 6 ч / $200, но результат несравнимо лучше. Evaluator окупается на задачах у границы возможностей модели |
| GameCraft-Bench (arXiv 2606.17861) | 140 Godot-задач, 15 жанров. Лучший агент всего 41,5%. Агенты реализуют узнаваемые механики, но **проваливают полноту контента, функциональную визуальную обратную связь и цельность презентации**. Значит, оценка должна смотреть на скриншоты и игровой процесс, а не только на «запускается» |
| GameDevBench (arXiv 2602.11103) | 333 задачи Godot 4. Лучшие ~67–69% (Claude Fable 5 в Claude Code — 67,3%). **Gameplay-задачи даются легче (46,9%), чем 2D-графика (31,6%).** Слабое место агентов: визуальная часть |
| Godot MCP-серверы (godot-mcp-runtime, hybridindie/godot-mcp, satelliteoflove/godot-mcp и др.) | Скриншоты, инъекция ввода, живое состояние, детерминированный playtest. Подтверждают выбранный подход. **Как зависимость не берём**: наш debug-слой проще, детерминированнее и не требует редактора. Можно подключить опционально как MCP для Engineer'а на этапе укрепления |
| Yandex SDK-аддоны для Godot 4 (ineedmypills/YandexGamesSDK4Godot, WebBus) | Подход «JavaScriptBridge + mock-режим в редакторе» подтверждён. Используем как reference для собственного тонкого моста, лицензию сторонних аддонов проверим перед вендорингом (открытый вопрос) |

### 1.3 Claude Code: что важно для Harness (по актуальной документации)

**Headless-режим** (`claude -p`, он же Agent SDK через CLI):
- `--output-format stream-json --verbose`: NDJSON-поток событий. Первое событие `system/init` (модель, инструменты, MCP, плагины, ошибки загрузки плагинов), последнее `result`.
- `result` содержит `session_id`, `num_turns`, `is_error`, `subtype` (`success`, `error_max_turns`, `error_max_budget_usd`, `error_during_execution`, …), `total_cost_usd`, `usage`, `modelUsage` (по моделям, включая субагентов), `permission_denials`.
- `--json-schema '<schema>'` + `--output-format json` → валидированный `structured_output`. **Решает проблему «кривого JSON» от Evaluator'а и Planner'а на уровне инструмента**, а не регулярками.
- `--model`, `--effort low|medium|high|xhigh|max`, `--fallback-model` (цепочка при перегрузке), `--max-turns`, `--max-budget-usd` (только print-режим, учитывает субагентов).
- `--resume <session_id>`, `--session-id <uuid>` (Harness сам задаёт ID → знает его ещё до первого события), `--fork-session`, `--no-session-persistence`.
- `--append-system-prompt-file` (роль из `agents/*.md`), `--settings <file|json>` (права и sandbox на конкретный запуск), `--setting-sources`, `--strict-mcp-config`, `--plugin-dir`, `--add-dir`.
- `--permission-mode dontAsk` + `--allowedTools` / `--disallowedTools`: всё, что не разрешено явно, отклоняется без вопроса. `--permission-prompts none` дополнительно говорит модели не повторять отклонённое.
- `system/api_retry` события с категорией ошибки (`rate_limit`, `overloaded`, `authentication_failed`, `billing_error`, …): основа для классификации инфраструктурных сбоев.
- **SIGINT** завершает текущий ход и записывает result; **SIGTERM** → код 143, ход не завершён, дерево Bash-процессов убивается; при `--resume` прерванный ход остаётся как есть.
- `--bare`: пропускает CLAUDE.md, хуки, skills, плагины, MCP из окружения пользователя. Рекомендуется для скриптов, **но работает только с `ANTHROPIC_API_KEY`** (не читает OAuth-логин подписки).

**Agent SDK для Python** (`claude-agent-sdk`): запускает тот же CLI как подпроцесс и общается по stdin/stdout. Даёт `ClaudeAgentOptions` (model, effort, max_turns, max_budget_usd, allowed/disallowed_tools, permission_mode, `can_use_tool`-callback, hooks на Python, cwd, env, resume/fork, output_format, sandbox, plugins, agents), `ResultMessage` с теми же полями стоимости, `interrupt()` у `ClaudeSDKClient`. Асинхронный API.

**Стоимость:** `total_cost_usd` и `costUSD` это **клиентская оценка** по встроенной таблице цен, не счёт. При `--resume` результат включает траты всей сессии (не суммировать повторно). При крэше поля могут быть нулями → нужен fallback по `usage` ассистентских сообщений (с дедупликацией по message id). При подписке это «эквивалентная стоимость API», реальный лимит у подписки свой.

**Права и изоляция:**
- Файловые инструменты ограничены рабочими директориями (`cwd` + `--add-dir`), правила `Read(...)`/`Edit(...)` с deny > ask > allow.
- **Bash-sandbox** (Linux/WSL2: bubblewrap + socat; macOS: seatbelt; нативный Windows: нет) ограничивает запись в cwd + temp, сеть через allowlist доменов, `denyRead` для секретов. `failIfUnavailable`, `allowUnsandboxedCommands: false` для строгого режима. Покрывает только shell-команды, не файловые инструменты и не MCP.
- В `-p` без `--bare` **хуки из `.claude/settings.json` и `.mcp.json` проекта выполняются без диалога доверия**. Репозиторий игры пишет агент → Harness обязан контролировать, что агент не создаст себе хук/MCP (запрет `Edit(.claude/**)`, `Edit(.mcp.json)` и проверка перед каждым запуском).

**Skills/plugins:** skills в `.claude/skills/` репозитория игры или через `--plugin-dir`. Caveman и любые пользовательские skills подключаются **только опционально**, флагом конфига; по умолчанию Harness изолирует запуск от `~/.claude` пользователя (см. 4.6).

### 1.4 Модели и цены (platform.claude.com, на 2026-10-05)

| Модель | ID | Вход / выход за MTok | Чтение кэша | Effort | Контекст | Комментарий |
|---|---|---|---|---|---|---|
| Fable 5.1 | `claude-fable-5-1` | $10 / $50 | $0.25 | да (default high) | 1M | для самых сложных long-horizon задач |
| Opus 5.5 | `claude-opus-5-5` | $4 / $20 | **$0.20** | да (default medium) | 1M | «по умолчанию для большинства нагрузок» по документации |
| Sonnet 5.5 | `claude-sonnet-5-5` | $2 / $10 | **$0.20** | да (default high) | 1M | лучшее соотношение скорость/интеллект |
| Haiku 4.5 | `claude-haiku-4-5-20251001` | $1 / $5 | $0.10 | **нет** | 200K | **retirement не раньше 2026-10-15**, знания до февраля 2025 |

Важное наблюдение: **в агентных сессиях доминирует чтение кэша**, а у Opus 5.5 и Sonnet 5.5 оно стоит одинаково ($0.20/MTok). Значит, реальная разница в цене Opus 5.5 и Sonnet 5.5 на длинной сессии Engineer'а заметно меньше номинальных 2×. Это аргумент не экономить на Opus там, где важна точность (Planner, Evaluator), и аргумент **проверить экспериментом**, не выгоднее ли Opus 5.5 для Engineer'а за счёт меньшего числа итераций (см. 6.3).

### 1.5 Godot и Web-экспорт

| Вопрос | Факт (godot-docs, master) | Решение |
|---|---|---|
| Версия | Последняя стабильная **4.7.2** (теги репозитория godotengine/godot). Godot 3 устарел для нового проекта | **Godot 4.7.x**, точная версия пинится в конфиге, `harness doctor` её проверяет |
| Рендерер | Web: только **WebGL 2.0 через Compatibility**. Forward+/Mobile в Web не поддерживаются, WebGPU нет | Шаблон сразу на `gl_compatibility`. Плюс: тот же OpenGL-рендерер работает под Xvfb с программным Mesa (llvmpipe) **без GPU**, значит скриншоты доступны на любом Linux-сервере |
| C# | «Projects written in C# using Godot 4 currently cannot be exported to the web» | **GDScript** со статической типизацией. Ловушки GDScript (Variant-инференция `:=` и т. п.) описываем в гайде движка |
| Потоки | С 4.3 **однопоточный экспорт по умолчанию**; многопоточный требует COOP/COEP-заголовков и полной cross-origin изоляции («no ads, nor third-party integrations») | **Только однопоточный.** Многопоточный несовместим с рекламой и SDK площадки |
| Аудио | В Web по умолчанию режим Sample: низкая задержка, но **нет AudioEffects, reverb, doppler** | Гайд запрещает аудио-эффекты на шинах. Автовоспроизведение браузеры блокируют: звук стартует после первого ввода |
| Размер | Пустой экспорт 4.x: wasm ~35–42 МБ без сжатия, ~9 МБ в zip; кастомный шаблон без лишних модулей уменьшает на порядок (сторонние замеры) | Укладываемся в лимит Yandex 100 МБ без сжатия, но время загрузки важно. **Кастомный облегчённый export template** выносим в этап укрепления (открытый вопрос) |
| JavaScriptBridge | `JavaScriptBridge.get_interface()`, `create_object()`, `create_callback()` (ссылку на callback нужно хранить), `eval()` | Мост к Yandex SDK и к debug-слою в Web-сборке |
| Фон/фокус | Браузер ставит вкладку на паузу (`_process` не вызывается). Fullscreen и захват курсора только из обработчика ввода | Учитываем в Platform-слое и тестах фокуса |
| Сохранения | `user://` в IndexedDB; в iframe нужны third-party cookies; в инкогнито не сохраняется | Сохранения через `Platform.save()` → облако Yandex SDK, локальный fallback |
| Headless | `--headless` = dummy display + dummy audio: **рендера нет**, скриншоты пустые | Логические тесты в `--headless`; скриншоты и видео под Xvfb (Linux) или в обычном окне (Windows/macOS) |
| CLI | `--headless --import`, `--check-only --script`, `--export-release "Web" <path>` (нужны export templates), `--write-movie`, `--fixed-fps`, `--quit-after`, `--log-file` | Все вызовы через одну обёртку `GodotRunner` с таймаутом и группой процессов |

### 1.6 Yandex Games (только для понимания будущего слоя)

> **Ограничение исследования.** Домены `yandex.ru` и `yandex.com` заблокированы сетевой политикой этого облачного окружения, поэтому страницы документации напрямую прочитать не удалось. Ниже **выдержки, полученные через поиск**, со ссылками на официальные страницы. На этапе 7 каждое требование нужно сверить с первоисточником (и, вероятно, с русской версией). Номера пунктов частично восстановлены по URL официальных страниц.

| Тема | Что известно | Источник | Автопроверка |
|---|---|---|---|
| SDK | SDK должен быть установлен; `LoadingAPI.ready()` вызывается, когда игра загрузила ресурсы и готова к взаимодействию (без экранов загрузки). `GameplayAPI.start()/stop()` необязательны, но если используются, то строго по моментам геймплея | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements), [1.19 SDK methods](https://yandex.com/dev/games/doc/en/requirements/1/19), [sdk-game-events](https://yandex.com/dev/games/doc/en/sdk/sdk-game-events) | да: mock-SDK в Playwright фиксирует вызовы и их порядок |
| Звук и фокус (1.3) | При потере фокуса звук останавливается; игра реагирует на потерю фокуса страницы | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements) | да: blur/visibilitychange в Playwright + состояние аудио из debug-слоя |
| Реклама | Во время fullscreen/rewarded звук и геймплей на паузе. Реклама только в логических паузах (4.4) | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements), [4.4 Ad placement](https://yandex.com/dev/games/doc/en/requirements/4/4), [sdk-adv](https://yandex.com/dev/games/doc/en/sdk/sdk-adv) | частично: mock-SDK вызывает `onOpen`/`onClose` → проверка паузы; «логичность места» смотрит человек/LLM |
| Сохранение прогресса (1.9) | Прогресс не теряется после перезагрузки страницы и смены ориентации | [1.9 Progress saving](https://yandex.com/dev/games/doc/en/requirements/1/9) | да: сценарий «сыграть → reload → проверить состояние» |
| Корректное отображение (1.10) | Корректный рендер при ресайзе и повороте, элементы не обрезаются и не выходят за экран | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements) | частично: скриншоты в нескольких вьюпортах + проверка rect'ов Control-нод из дампа |
| Технические сообщения (1.14) | Отдельная страница, содержание **не сверено** | [1.14](https://yandex.com/dev/games/doc/en/requirements/1/14) | TBD |
| Стабильность | Нет ошибок, зависаний, крэшей, в том числе во время показа рекламы | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements) | да: консоль браузера, лог Godot |
| Мобильные | На мобильных полноэкранный режим, управление полностью жестами | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements) | частично: мобильная эмуляция + тач-сценарий |
| Язык | Автоопределение языка через SDK (`environment.i18n.lang`, ISO 639-1) | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements), [sdk-environment](https://yandex.com/dev/games/doc/en/sdk/sdk-environment) | да: mock-SDK с разными `lang` |
| Облачные сохранения | `player.setData()`, до 200 КБ на игрока; при использовании включить опцию в черновике | [sdk-player](https://yandex.com/dev/games/doc/en/sdk/sdk-player) | да: размер сериализованных данных |
| Ссылки | Нет ссылок на другие игры разработчика | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements) | да: статический поиск URL |
| Архив | Не больше 100 МБ в несжатом виде; `index.html` в корне архива; имена файлов без пробелов и кириллицы | [requirements](https://yandex.com/dev/games/doc/en/concepts/requirements) | да: чисто детерминированно |
| Черновик | Иконка 512×512 PNG, обложка 800×470 PNG, скриншоты; обязательные поля: название, описание, «как играть», версия, категории, иконка, обложка, архив, платформы, ориентация. Скриншоты нельзя использовать как иконку/обложку | [draft](https://yandex.com/dev/games/doc/en/console/add-new-game/draft) | да: размеры/формат; «не скриншот» проверяет LLM/человек |
| Контент (8.3.x) | Ограничения по неприятному и шокирующему контенту и др. | [8.3.5](https://yandex.com/dev/games/doc/en/requirements/8/3/5), [8.3.6](https://yandex.com/dev/games/doc/en/requirements/8/3/6) | ручная проверка / LLM |

Неподтверждённое (встречено только у третьих лиц, **не вносить без сверки**): адаптация под ТВ (`deviceInfo.type === 'tv'`, навигация пультом), трактовка пункта о «системном плеере» (только Web Audio), «перевод на русский обязателен».

---

## 2. Слабые места исходной идеи (original_brief)

1. **Слишком много ролей подряд** (Analyst → Architect → Engineer → Skeptic → Fix → QA → Yandex Auditor → Store → Final Auditor). Каждая передача теряет контекст и стоит денег. Референсные проекты эволюционировали в обратную сторону.
2. **Skeptic как мнение LLM без инструментов.** Найденные «проблемы» без воспроизведения порождают ложные исправления. Критика должна опираться на прогон.
3. **Yandex в самом конце.** Пауза на фокусе, сохранения, `LoadingAPI.ready`, реклама в логических паузах влияют на архитектуру игры (жизненный цикл, сохранения, аудио-шины). Переделывать это в конце дорого.
4. **Engineer «работает с Git»** конфликтует с чекпоинтами и откатом Harness. Кто владеет историей, должно быть однозначно.
5. **Монетизация в GDD до того, как подтверждён интерес core loop.** Монетизация нужна как ограничение архитектуры (где логические паузы), а не как фича прототипа.
6. **Final Auditor как LLM.** Вердикт READY должен быть в основном детерминированным: все проверки пройдены на текущем HEAD, все вердикты свежие.
7. **Не определено, как проверять игру программно.** Без debug-слоя «QA» сводится к «проект открылся». PLAN_v2 это исправил.

## 3. Где я не согласен с PLAN_v2 и почему

| # | PLAN_v2 | Предложение | Почему |
|---|---|---|---|
| 1 | BUILD: цикл **по задачам** IMPLEMENT → VERIFY → EVALUATE → FIX | Цикл **по вехам** (2–5 на игру). Внутри вехи Engineer сам декомпозирует и ведёт `PROGRESS.md`; независимая оценка в конце вехи | Godogen удалил свой decomposer; Anthropic убрал per-sprint декомпозицию по мере роста моделей; GameForge лучше без промежуточных repair-петель. Оценка каждой мелкой задачи умножает стоимость (каждая сессия Evaluator'а заново читает проект) и дробит контекст Engineer'а |
| 2 | Yandex-слой только на этапе 7 | Платформенный слой (`Platform` autoload: mock/yandex, пауза аудио на фокусе и рекламе, сохранения через сервис) **в шаблоне с первого дня**; Yandex-валидаторы и аудит на этапе 7, как в плане | Иначе в конце придётся переписывать жизненный цикл игры (см. раздел 2, п. 3) |
| 3 | Evaluator на Sonnet 5.5 (та же модель, что и Engineer) | Evaluator на **Opus 5.5, medium** | Сессии Evaluator'а короткие и в основном читают (дёшево при кэше $0.20). Другая модель уменьшает коррелированные слепые пятна генератора и судьи. Ошибка Evaluator'а дороже: ложный PASS пропускает баг, ложный FAIL запускает лишнюю итерацию Engineer'а |
| 4 | Final gate на Haiku 4.5 | Детерминированный чеклист + короткое заключение на **Sonnet 5.5, low** | Haiku 4.5: retirement «не раньше 2026-10-15» (через 10 дней), нет effort, знания до февраля 2025. Строить на нём ядро рискованно |
| 5 | Engineer пишет сценарии, Evaluator проверяет, что они есть и проходят | Плюс **приёмочные сценарии Evaluator'а** вне репозитория игры, через Game Contract. Плюс **контроль целостности** тест-раннера (хэш `addons/harness/`) | Тесты, написанные исполнителем, не могут быть единственным доказательством: возможна (даже неумышленная) подгонка тестов под реализацию |
| 6 | HarnessDebug как autoload игры | Разделить: (a) **тест-раннер принадлежит Harness** (`addons/harness/`, read-only для агента, синхронизируется из шаблона перед каждым прогоном); (b) в игре только **тонкий провайдер состояния** по контракту; (c) в release-сборке debug-слой инертен и исключён из экспорта | Код отладки и чит-команды не должны попасть в публикуемую сборку; агент не должен иметь возможности «ослабить» проверку |
| 7 | Xvfb нужен для скриншотов (неявно: нужен GPU) | Compatibility-рендерер работает на программном Mesa под Xvfb, GPU не обязателен; видео опционально | Снимает требование GPU-сервера для MVP |
| 8 | Docker-изоляция только на этапе 9 | На Linux с этапа 2 включаем **Bash-sandbox Claude Code** (bubblewrap) + per-run settings с deny-правилами. Docker остаётся на этапе 9 | Дешёвая изоляция «из коробки», закрывает основные риски (запись вне проекта, утечка секретов, сеть) |
| 9 | `run` и `resume` отдельные команды | `run` идемпотентен и всегда продолжает с последнего согласованного состояния; `resume` оставляем как алиас | Меньше режимов, меньше ошибок восстановления |
| 10 | ASSETS отдельной стадией после BUILD | Стиль и бюджет ассетов фиксируются в PLAN; плейсхолдеры с **финальными размерами** с первой вехи; отдельная веха ASSETS для замены и полировки | GameDevBench/GameCraft: визуальная часть самое слабое место агентов, её нельзя откладывать в самый конец |

С остальным в PLAN_v2 согласен: журнал событий, состояние вне репо игры, Web с первого прототипа, гейт человека после прототипа, объединение Skeptic/QA/Evaluator, лимиты и circuit breaker.

---

## 4. Итоговая архитектура

### 4.1 Компоненты

```text
                     ┌──────────────────────────── Harness (Python) ─────────────────────────────┐
 harness CLI ──────▶ │ Orchestrator (state machine, гейты, бюджеты, эскалация, circuit breaker)  │
                     │    │            │                 │                     │                   │
                     │    ▼            ▼                 ▼                     ▼                   │
                     │ StateStore   PromptBuilder    AgentRunner ─────▶ adapters: cli | sdk | mock │
                     │ (events.jsonl agents/*.md +   (роль→модель,       │                         │
                     │  + snapshot) артефакты)        права, лимиты)     ▼                         │
                     │    │                                        Claude Code (процесс)           │
                     │    ▼                                              │ работает в repo/        │
                     │ GitOps (теги, откат)   Verify: GodotRunner, ScenarioRunner, WebRunner,     │
                     │                        Screenshots, YandexValidators  ◀── только инструменты│
                     └────────────────────────────────────────────────────────────────────────────┘
                                               │
                                     workspace/game_001/repo  (Godot-проект, Git)
```

Принцип разделения: **Harness решает, когда и кого запускать и что считать готовым. Claude Code решает, как писать игру. Инструменты решают, работает ли она.**

### 4.2 Роли

| Роль | Вход | Выход | Права в repo | Модель по умолчанию |
|---|---|---|---|---|
| **Planner** | идея, ограничения платформы, шаблон | `GDD.md`, `ACCEPTANCE.yaml` (критерии с id), `CONTRACT.yaml` (Game Contract), `PLAN.md` (вехи, порядок, стиль и бюджет ассетов). Структурированная часть через `--json-schema` | только чтение шаблона; файлы пишет Harness | Opus 5.5, high |
| **Engineer** | веха из `PLAN.md`, Contract, отчёт о провалах (при FIX) | код/сцены/ассеты, сценарии механик, обновлённый `docs/PROGRESS.md` | запись в repo, кроме защищённых путей | Sonnet 5.5, medium (high на повторе) |
| **Evaluator** | критерии вехи, evidence-пакет от инструментов (логи, отчёты сценариев, скриншоты, дампы, Web-отчёт), diff вехи | `EvalReport` (JSON по схеме): PASS/FAIL, issues с severity, воспроизведением, ссылкой на критерий и доказательство; может написать **дополнительные «ломающие» сценарии** в свою scratch-папку и прогнать их | **без записи в repo**; запуск только команд проверки Harness | Opus 5.5, medium |
| **Debugger** (эскалация) | то же, что Engineer + история неудачных попыток | сначала диагноз (гипотеза + подтверждение), потом исправление | как Engineer | Opus 5.5, high → xhigh |
| **Release writer** | GDD, скриншоты, требования к черновику | метаданные (RU/EN), подписи, выбор скриншотов; иконку/обложку генерирует или собирает Asset-пайплайн | без записи в код игры | Sonnet 5.5, low |
| **Final gate** | всё | READY/FAILED: детерминированный чеклист + короткое заключение | нет | код + Sonnet 5.5, low |

Asset-агент как отдельная роль появится на этапе 6, если понадобится; до этого ассеты делает Engineer по стратегии из раздела 6.

### 4.3 State machine

```text
CREATED ──▶ SPEC ──▶ PLAN ──▶ MILESTONE(1 = PROTOTYPE) ──▶ [HUMAN_REVIEW] ──▶ MILESTONE(2..N) ──▶ YANDEX_AUDIT ──▶ RELEASE_PREP ──▶ FINAL_GATE ──▶ READY
                                     │                                                │                 │                                   │
                                     └──── внутри каждой вехи: ────────────────────────┘                 └── FIX-петля как в вехе ──┘            └─▶ FAILED
                                            IMPLEMENT ─▶ VERIFY ─▶ EVALUATE ─┬─ PASS ─▶ CHECKPOINT ─▶ следующая веха
                                                          ▲                    └─ FAIL ─▶ FIX (attempt k, tier t) ─┐
                                                          └────────────────────────────────────────────────────────┘

Служебные состояния (из любого рабочего): BLOCKED (нужен человек), PAUSED (исчерпан бюджет/лимит, можно продлить), FAILED (терминально)
Откат: событие ROLLBACK (не состояние) → возврат к тегу чекпоинта и к соответствующему состоянию
```

- **SPEC/PLAN** у Planner'а одной сессией или двумя (`--resume` той же сессии, чтобы не перечитывать контекст). Разделение на Analyst/Architect только если качество плана окажется проблемой.
- **VERIFY** полностью детерминирован, без LLM: import, `--check-only`, smoke, сценарии Engineer'а, приёмочные сценарии вехи, скриншоты, Web-сборка и Playwright (с вехи PROTOTYPE), Yandex-валидаторы (с этапа 7).
- **EVALUATE** запускается, только если VERIFY не провалился на «жёстких» гейтах (не собирается, не запускается). Иначе сразу FIX с логом: тратить Evaluator на некомпилируемый проект бессмысленно.
- **Переход из FIX** только обратно в VERIFY. Выход из петли: PASS, лимит (→ эскалация → BLOCKED/PAUSED), circuit breaker (→ BLOCKED).
- **HUMAN_REVIEW** после PROTOTYPE: `harness approve | revise "<комментарий>" | stop`. Отключается в конфиге.
- Каждое состояние имеет **критерий выхода**, записанный в событии (какие проверки, на каком SHA).

### 4.4 Game Contract

Файл `CONTRACT.yaml` создаётся Planner'ом и лежит в `repo/docs/` (его видят все):

```yaml
input_actions: [move_left, move_right, jump, pause]      # имена в InputMap; тач-кнопки шлют те же actions
state:                                                    # что отдаёт провайдер состояния (game_state())
  scene: string           # имя текущего экрана: menu | level | game_over ...
  player.position: vec2
  player.alive: bool
  score: int
  level: int
commands:                                                 # debug-команды, доступные только в тестовой сборке
  set_level: {args: [int]}
  teleport_player: {args: [vec2]}
  kill_player: {}
platform_events: [ad_open, ad_close, focus_lost, focus_gained]
```

Engineer реализует контракт (функция `game_state()` у автозагрузки `Game` и обработчики команд). Evaluator и приёмочные сценарии работают **только через контракт**, поэтому не ломаются от рефакторинга сцен. Изменение контракта Engineer'ом возможно только через запрос (событие `CONTRACT_CHANGE_REQUESTED`), который принимает Planner/человек.

### 4.5 Поток данных

```text
идея ─▶ Planner ─▶ artifacts/ (GDD, ACCEPTANCE, CONTRACT, PLAN) ─▶ копия в repo/docs/
веха k ─▶ PromptBuilder(role=engineer, milestone=k, contract, [fix_report]) ─▶ Claude Code в repo/
         ◀─ result (session_id, cost, turns, subtype) ─▶ events.jsonl, runs/<run_id>/transcript.jsonl
Harness ─▶ git commit + тег h/<step_id>
Verify ─▶ reports/<sha>/{verify.json, scenarios/*.json, screenshots/*.png, godot.log, web/console.json}
Evaluator(evidence-пакет + критерии вехи) ─▶ EvalReport(sha) ─▶ reports/<sha>/eval.json
PASS ─▶ тег cp/<milestone> ;  FAIL ─▶ fix_report = issues + доказательства ─▶ Engineer (attempt k+1)
```

Engineer'у передаётся отчёт об ошибках с воспроизведением и ссылками на доказательства, но **не** исходники приёмочных сценариев Evaluator'а (только их шаги в человекочитаемом виде). Так он чинит причину, а не подгоняет под тест.

### 4.6 ClaudeCodeRunner

Интерфейс (псевдокод):

```python
class AgentRunner(Protocol):
    def run(self, req: AgentRequest) -> AgentResult: ...
    def cancel(self, run_id: str) -> None: ...

@dataclass
class AgentRequest:
    run_id: str; role: str; prompt: str; system_append_file: Path
    model: str; effort: str | None; fallback_models: list[str]
    cwd: Path; add_dirs: list[Path]; env: dict[str, str]
    allowed_tools: list[str]; disallowed_tools: list[str]; settings: dict   # права и sandbox на этот запуск
    json_schema: dict | None; max_turns: int | None; max_budget_usd: float | None
    timeout_s: int; resume_session_id: str | None; session_id: str          # Harness задаёт UUID сам
    transcript_path: Path

@dataclass
class AgentResult:
    status: Literal["ok", "agent_error", "timeout", "budget", "max_turns", "infra_error", "cancelled"]
    session_id: str; exit_code: int | None; structured_output: dict | None; text: str | None
    cost_usd_estimate: float; usage_by_model: dict; num_turns: int; duration_s: float
    permission_denials: list; infra_error_kind: str | None   # rate_limit, overloaded, auth, network...
```

Адаптеры:
- **`cli` (основной для MVP):** `subprocess.Popen(["claude", "-p", ..., "--output-format", "stream-json", "--verbose", "--session-id", uuid, ...])`. Поток построчно пишется в `transcript.jsonl` (вместе с последним `result` это и есть лог запуска), параллельно парсятся `system/init`, `system/api_retry`, `result`. Плюсы: синхронный и прозрачный, легко убить, не зависит от версии Python SDK, тот же бинарь, что у пользователя.
- **`sdk`:** `claude-agent-sdk` (тоже поднимает CLI). Плюсы: `can_use_tool` и хуки на Python (политика прав в коде Harness), `interrupt()`. Минус: асинхронность и ещё одна зависимость. Делаем вторым, когда понадобится тонкая политика прав.
- **`mock`:** воспроизводит заранее записанные сценарии (создаёт файлы, возвращает заданный result/ошибку/таймаут). Нужен для тестов Harness и проверки восстановления без трат.

Запуск и завершение процессов спрятаны в `harness/platform/proc.py`:
- POSIX: `start_new_session=True`; по таймауту SIGINT (Claude завершает ход и пишет result) → ждём 20 с → SIGTERM группе → SIGKILL.
- Windows: `CREATE_NEW_PROCESS_GROUP`; `CTRL_BREAK_EVENT` → `taskkill /T /F /PID`.
- Таймаут «нет вывода N минут» (watchdog по потоку) отдельно от общего таймаута.

Изоляция запуска от окружения пользователя:
- Аутентификация: `ANTHROPIC_API_KEY` **или** OAuth-токен подписки (`claude setup-token` → `CLAUDE_CODE_OAUTH_TOKEN`). С API-ключом используем `--bare` (полная изоляция от `~/.claude`). С подпиской `--bare` недоступен → `--setting-sources project` + `--strict-mcp-config` + свой `--settings`, и тогда пользовательские skills/CLAUDE.md не подтягиваются.
- `inherit_user_claude_config: false` по умолчанию. Включается флагом, если пользователь хочет Caveman/свои skills. Harness от них не зависит.
- `env` формируется **с нуля**: PATH, HOME, нужный токен, `GODOT_BIN`, `HARNESS_*`. Остальные переменные (в том числе ключи генерации ассетов) передаются только ролям, которым нужны, и никогда не попадают в логи (маскирование по списку имён).

### 4.7 Права по ролям (по умолчанию)

Режим `--permission-mode dontAsk` + `--permission-prompts none` + allowlist. Общий deny для всех ролей: `Edit(.claude/**)`, `Edit(.mcp.json)`, `Edit(addons/harness/**)`, `Bash(git push *)`, `Bash(git reset *)`, `Bash(git checkout *)`, `Bash(git rebase *)`, `Bash(git tag *)`, чтение `.env`/секретов.

| Роль | Разрешено |
|---|---|
| Planner | Read, Glob, Grep; без Bash и записи (артефакты возвращаются через `--json-schema`, файлы пишет Harness) |
| Engineer / Debugger | Read, Edit, Write, Glob, Grep; `Bash(godot *)` через обёртку `tools/godot` с таймаутом; `Bash(git status *)`, `Bash(git diff *)`, `Bash(git log *)`, `Bash(git add *)`, `Bash(git commit *)`; `Bash(python3 tools/*)`; команды проверки Harness (`harness-tools test …`) |
| Evaluator | Read, Glob, Grep; `Bash(harness-tools test *)` и `Write` только в свою scratch-папку вне repo (`--add-dir`) |
| Release writer | Read, Glob; запись только в `release/` вне repo |

Bash-sandbox на Linux: запись только cwd + temp, `denyRead` для `~/.ssh`, `~/.aws`, `~/.config` и т. п., сеть закрыта (allowlist пуст; для генерации ассетов через API отдельный инструмент Harness, не прямой интернет у агента). На Windows sandbox недоступен: Harness пишет предупреждение в `doctor` и в событие запуска.

Перед каждым запуском Harness проверяет, что в repo не появились `.claude/settings*.json` с хуками и `.mcp.json` (их агенту создавать запрещено; если появились, это нарушение, файл удаляется, событие `POLICY_VIOLATION`).

### 4.8 Git и чекпоинты

- **Историей владеет Harness.** Engineer может коммитить внутри шага (это помогает ему самому), но теги ставит только Harness, а reset/checkout/push агенту запрещены.
- После каждого шага агента: `git add -A && git commit` (если есть изменения) + лёгкий тег `h/<step_id>`.
- Именованные чекпоинты по итогам гейтов: `cp/spec`, `cp/plan`, `cp/prototype`, `cp/m2`, …, `cp/yandex-pass`, `cp/release-candidate`.
- Откат: `harness rollback game_001 --to cp/prototype` → `git reset --hard` + `git clean -fd` (кроме `.godot/`, кэш импорта), событие `ROLLBACK` в журнал **вне** repo, state возвращается к состоянию, соответствующему тегу. История отката не теряется.
- Перед каждым шагом агента: рабочее дерево должно быть чистым (иначе это следы прерванного шага, см. 4.10).

### 4.9 Состояние и журнал событий

Рабочая папка (корень задаётся `HARNESS_WORKSPACE` или конфигом; по умолчанию `./workspace`, она в `.gitignore` репозитория Harness):

```text
workspace/
  game_001/
    repo/                         Git-репозиторий игры (Godot-проект)
    harness/
      .lock                       pid + hostname + время; защита от двух процессов на один проект
      events.jsonl                append-only журнал, источник истины
      state.json                  снапшот = свёртка журнала (пересобирается при расхождении)
      artifacts/                  GDD.md, ACCEPTANCE.yaml, CONTRACT.yaml, PLAN.md (оригиналы от Planner'а)
      reports/<sha>/              verify.json, eval.json, scenarios/, screenshots/, web/, yandex/
      runs/<run_id>/              transcript.jsonl, prompt.md, request.json (без секретов), stderr.log
      evaluator_scratch/          дополнительные сценарии Evaluator'а
      release/                    метаданные, иконка, обложка, скриншоты, zip
```

Событие:

```json
{"seq": 57, "ts": "2026-10-05T12:00:00Z", "type": "AGENT_FINISHED", "project": "game_001",
 "state": "MILESTONE", "milestone": "m1", "step_id": "m1.fix.2", "run_id": "r-0031",
 "data": {"role": "engineer", "model": "claude-sonnet-5-5", "effort": "high", "status": "ok",
          "cost_usd_estimate": 1.84, "turns": 42, "session_id": "…", "commit": "a1b2c3d"}}
```

Типы событий (основные): `PROJECT_CREATED`, `STATE_ENTERED`, `STEP_STARTED`, `AGENT_STARTED`, `AGENT_FINISHED`, `AGENT_FAILED`, `VERIFY_FINISHED`, `EVAL_FINISHED`, `CHECKPOINT_CREATED`, `ROLLBACK`, `ESCALATED`, `BUDGET_UPDATED`, `BUDGET_EXCEEDED`, `CIRCUIT_OPEN`, `BLOCKED`, `HUMAN_DECISION`, `POLICY_VIOLATION`, `CONTRACT_CHANGE_REQUESTED`, `STEP_FINISHED`.

Запись: одна строка JSON, `flush` + `fsync`. Повреждённая последняя строка (обрыв записи) при чтении отбрасывается с предупреждением. `state.json` пишется атомарно (tmp + `os.replace`).

`state.json` (проекция): текущее состояние и веха, номер попытки и tier эскалации, бюджет (потрачено/лимиты на проект и вехи), последний зелёный чекпоинт, открытый шаг (если есть), счётчики отпечатков ошибок для circuit breaker.

### 4.10 Восстановление

| Сбой | Как обнаруживается | Действие |
|---|---|---|
| Harness убит / перезагрузка машины | есть `STEP_STARTED` без `STEP_FINISHED`; stale `.lock` (pid мёртв) | см. «прерванный шаг» |
| Прерванный шаг агента | открытый шаг + грязное дерево | Если известен `session_id` и это первое прерывание: **resume той же сессии** («продолжи; проверь состояние перед продолжением»). Иначе откат к тегу начала шага и повтор. Повторы прерываний считаются |
| Claude Code упал / `error_during_execution` | `subtype`, код выхода | повтор шага (лимит retry), стоимость из `usage` если result нулевой |
| Таймаут / зависание | общий таймаут или watchdog «нет вывода» | SIGINT → SIGTERM → SIGKILL, коммит частичной работы в отдельный тег `h/<step>-partial`, повтор с указанием, что предыдущая попытка не уложилась |
| Сеть / rate limit / overloaded / auth | `system/api_retry`, `infra_error_kind` | **не считается итерацией исправления**; экспоненциальная пауза, после N подряд → PAUSED с причиной. `auth`/`billing` → сразу BLOCKED |
| Невалидный JSON | `--json-schema` гарантирует схему; если всё равно нет `structured_output` | один повтор с `--resume` и просьбой вернуть результат; затем `AGENT_FAILED` |
| Godot упал / завис | код ≠ 0, таймаут `GodotRunner` (exit 124), `ERROR:`/`SCRIPT ERROR` в логе | это **дефект игры**, а не сбой Harness: идёт в отчёт VERIFY → FIX |
| Сбой оценщика | Evaluator не вернул отчёт / противоречит детерминированным фактам | повтор; затем BLOCKED с классом `evaluator_failure` (не тратит итерации Engineer'а) |

### 4.11 Бюджеты и ограничители

Конфиг `config/harness.yaml` (значения по умолчанию предварительные, уточняются калибровкой на этапе 5):

```yaml
budget:
  project_usd: 40            # оценка по total_cost_usd; при подписке это эквивалент API-стоимости
  per_milestone_usd: 12
  per_run_usd: 6             # → --max-budget-usd для каждого запуска
limits:
  fix_attempts_per_milestone: 4      # включая эскалацию
  escalate_after_attempts: 2
  run_timeout_min: {planner: 20, engineer: 60, evaluator: 20}
  idle_output_timeout_min: 10
  max_turns: {planner: 40, engineer: 250, evaluator: 80}
  circuit_breaker_same_fingerprint: 3
  no_progress_attempts: 2            # пустой diff или тот же набор проваленных проверок
gates:
  human_review_after_prototype: true
```

**Отпечаток ошибки** для circuit breaker: id проваленной проверки + нормализованное сообщение (без чисел, путей, адресов). Три одинаковых подряд → BLOCKED с отчётом. **Нет прогресса**: diff попытки пустой или множество проваленных проверок не уменьшилось → эскалация раньше лимита.

---

## 5. Инфраструктура проверки игры

Это ядро, поэтому подробно.

### 5.1 Шаблон игры (`templates/godot/`)

```text
project.godot            Compatibility-рендерер, stretch mode canvas_items + aspect expand, ориентация из PLAN,
                         строгие GDScript-предупреждения (untyped declaration и т. п.) как ошибки
export_presets.cfg       пресет "Web" (однопоточный, свой HTML-шелл), пресет "Web-Test" (с feature tag harness)
web/shell.html           HTML-шелл: подключение SDK площадки, мост, сигнал готовности
autoload/
  Game.gd                ← пишет Engineer: game_state() и обработчики команд по CONTRACT.yaml
  Platform.gd            фасад: init(), loading_ready(), gameplay_start/stop(), show_interstitial(),
                         show_rewarded(), save()/load(), language(); бэкенды mock | yandex
  AudioService.gd        шины Master/Music/SFX; mute по focus_lost и ad_open, unmute по обратным событиям
addons/harness/          ПРИНАДЛЕЖИТ HARNESS, read-only для агента, хэш проверяется перед прогоном:
  probe.gd               автозагрузка-пробник: активна только при флаге запуска или feature tag "harness"
  runner.gd              SceneTree-скрипт исполнения сценариев (см. 5.2)
  web_bridge.gd          в Web-Test сборке публикует window.harness = {state(), command(), events()}
tests/scenarios/         сценарии механик (пишет Engineer)
tools/godot              обёртка с таймаутом (из шаблона)
docs/                    GDD.md, CONTRACT.yaml, PLAN.md, PROGRESS.md (ведёт Engineer), ASSETS.md (манифест)
CLAUDE.md                гайд движка: только то, что модель не знает (ловушки GDScript, Web-ограничения,
                         правила тестов и контракта, запреты)
```

**Release-сборка:** пресет `Web` исключает `addons/harness/*` и `tests/*` фильтром экспорта, а `probe.gd` дополнительно ничего не делает без feature tag `harness`. Валидатор проверяет, что в итоговом `.pck` нет путей `addons/harness`.

### 5.2 Сценарии

Формат (YAML), общий для сценариев Engineer'а и Evaluator'а:

```yaml
id: jump_over_gap
covers: [AC-3]                 # id критериев из ACCEPTANCE.yaml
seed: 42
fps: 60                        # --fixed-fps, детерминизм
setup:
  - command: set_level [1]
steps:
  - wait_frames: 30
  - hold: {action: move_right, frames: 90}
  - press: {action: jump}
  - hold: {action: move_right, frames: 60}
  - screenshot: after_jump     # только в режиме с рендером
assert:
  - "state.player.alive == true"
  - "state.player.position.x > 600"
  - "errors.count == 0"        # ошибки и предупреждения из лога движка
timeout_frames: 600
```

Исполнение: `godot --headless --path repo --fixed-fps 60 --script addons/harness/runner.gd -- --scenario <file> --out <json>` для логики; без `--headless` под Xvfb (Linux) или в окне (Windows/macOS) для сценариев со скриншотами. Ввод через `Input.action_press/parse_input_event` (клавиатура, мышь, `InputEventScreenTouch` для тача). Утверждения вычисляются в Python по JSON-дампу состояния (не `eval` в игре), язык выражений минимальный и безопасный.

Классы проверок VERIFY (по порядку, каждая со своим таймаутом):
1. **Структура**: `project.godot` есть, main scene задана, контракт и шаблонные файлы на месте, хэш `addons/harness` совпадает.
2. **Импорт и парсинг**: `--headless --import`, `--check-only` по всем `.gd`, ноль `SCRIPT ERROR`/`Parse Error`.
3. **Smoke**: старт main scene, N секунд без ошибок, `game_state()` отвечает и соответствует схеме контракта.
4. **Сценарии Engineer'а** (`tests/scenarios/`).
5. **Приёмочные сценарии вехи** (Evaluator/Planner, вне repo).
6. **Скриншоты** ключевых экранов в 2–3 разрешениях (десктоп 16:9, мобильный портрет/альбом), дамп rect'ов Control-нод для проверки «не обрезано».
7. **Web** (с вехи PROTOTYPE): экспорт `Web-Test` → локальный HTTP-сервер (COOP/COEP не нужны) → Playwright/Chromium: загрузка до сигнала готовности, ноль ошибок консоли, время загрузки, скриншот; мобильная эмуляция с тачем; смена ориентации; `blur`/`visibilitychange` → аудио приглушено (через `window.harness.state()`); reload → прогресс сохранён. Mock-SDK площадки внедряется в страницу и журналирует вызовы.
8. **Yandex-валидаторы** (этап 7).

Для Windows-ноутбука: всё, кроме Xvfb, работает так же; скриншоты в обычном окне, Playwright ставится через pip.

### 5.3 Evaluator

Получает evidence-пакет (пути к отчётам, скриншотам, логам, diff вехи, критерии), читает код и скриншоты (инструмент Read умеет изображения), может написать **дополнительные сценарии «на слом»** (граничные случаи, спам ввода, пауза в неудобный момент, смерть во время перехода) в свою scratch-папку и прогнать их той же командой. Возвращает `EvalReport` по JSON-схеме:

```json
{"sha": "a1b2c3d", "milestone": "m1", "verdict": "FAIL",
 "criteria": [{"id": "AC-3", "status": "pass|fail|inconclusive", "evidence": ["reports/a1b2c3d/scenarios/jump_over_gap.json"]}],
 "issues": [{"severity": "critical|major|minor", "category": "crash|softlock|logic|ui|controls|perf|visual|platform",
             "description": "...", "reproduction": "scenario: evaluator_scratch/spam_jump.yaml", "expected": "...",
             "actual": "...", "evidence": ["..."], "suggested_fix": "..."}]}
```

Правила: вердикт PASS невозможен, если детерминированные проверки красные (Harness это форсирует); `inconclusive` не равно `pass`; вердикт валиден только для своего SHA.

### 5.4 Видео

Опционально (конфиг): `--write-movie` + `--fixed-fps` + ffmpeg, 15–20 с демонстрационного сценария для человека на HUMAN_REVIEW и для релиза. На программном рендере медленно, поэтому не входит в обязательный VERIFY.

---

## 6. Модели: routing и эскалация

### 6.1 Конфиг (`config/models.yaml`, единственное место с model ID)

```yaml
models:
  frontier: claude-fable-5-1
  strong:   claude-opus-5-5
  standard: claude-sonnet-5-5
roles:
  planner:        {model: strong,   effort: high}
  engineer:       {model: standard, effort: medium}
  evaluator:      {model: strong,   effort: medium}
  debugger:       {model: strong,   effort: high}
  release_writer: {model: standard, effort: low}
  final_gate:     {model: standard, effort: low}
fallback: [standard]            # → --fallback-model при перегрузке
escalation:
  - {tier: 0, role: engineer, effort: medium}
  - {tier: 1, role: engineer, effort: high}       # тот же контекст: --resume сессии + отчёт
  - {tier: 2, role: debugger, effort: high}       # свежая сессия, сначала диагноз
  - {tier: 3, role: debugger, effort: xhigh, model: frontier, enabled: false}
```

### 6.2 Политика эскалации

```text
FAIL (attempt 1) ─▶ tier 0 повтор с отчётом
FAIL (attempt 2) ─▶ tier 1: та же модель, выше effort, продолжение сессии
FAIL / нет прогресса ─▶ tier 2: Opus 5.5 «Debugger», новая сессия: диагноз → подтверждение → фикс
FAIL ─▶ (tier 3 Fable 5.1, если включён) ─▶ BLOCKED с отчётом: что пробовали, отпечатки ошибок, стоимость
```

Сигналы «сложности», ускоряющие эскалацию: тот же отпечаток ошибки второй раз; пустой diff; рост числа проваленных проверок (регрессия → сначала откат к началу вехи, потом эскалация); `error_max_turns`.

### 6.3 Калибровка (этап 5)

Номинально Opus 5.5 в 2 раза дороже Sonnet 5.5, но чтение кэша у них одинаковое. На одном и том же наборе 3 простых игр сравнить Engineer = Sonnet 5.5 medium/high и Opus 5.5 medium по итоговой стоимости до PASS и числу итераций. Решение по умолчанию принимаем по данным, а не по прайсу.

---

## 7. Стратегия ассетов

Цель MVP: **нет серых прямоугольников, но и нет платных зависимостей по умолчанию.**

1. **Стиль задаёт Planner** в `PLAN.md`: палитра, стиль (flat/pixel/vector), базовое разрешение, размеры объектов в пикселях. Без этого ассеты из разных источников не сочетаются.
2. **Уровень 0, процедурная графика и звук (по умолчанию, бесплатно, офлайн):** `_draw()`/`Polygon2D`/`StyleBox`/шейдеры для геометрического стиля; SVG, которые пишет сама модель (Godot импортирует SVG); звуки через sfxr-подобный генератор (Python-скрипт в `tools/`, детерминированный по seed). Для многих казуальных web-игр этого достаточно.
3. **Уровень 1, CC0-библиотеки:** локальный кэш паков (например, Kenney — CC0) с индексом по тегам; Harness копирует выбранные файлы в repo, лицензия и источник записываются в `docs/ASSETS.md`. Только лицензии из allowlist (CC0, CC-BY с атрибуцией в метаданных релиза).
4. **Уровень 2, генерация (опционально, платно):** адаптер к API генерации изображений (по образцу Godogen `asset-gen`), ключи только в env, **бюджет и подтверждение трат** (гейт человека или лимит в конфиге), обязательная обработка: удаление фона, нарезка, даунскейл до целевого размера. Иконка 512×512 и обложка 800×470 для релиза, скорее всего, потребуют этого уровня или ручной работы.
5. **Манифест `docs/ASSETS.md`**: имя, путь, размер в игре, источник, лицензия, стоимость. Валидатор проверяет, что каждый файл в `assets/` есть в манифесте и лицензия из allowlist.
6. **Проверка:** скриншоты + Evaluator оценивает визуальную цельность (это слабое место агентов по GameDevBench/GameCraft-Bench).

---

## 8. Структура репозитория Harness

```text
ai-game-harness/
  pyproject.toml                  uv; Python ≥ 3.11; зависимости минимальные: pyyaml, jsonschema, typer/argparse, playwright (extra)
  harness/
    cli.py                        doctor | create | run | resume | status | test | audit | approve | revise | rollback | release | logs | budget
    config.py                     загрузка и валидация config/*.yaml, пути через pathlib
    orchestrator/
      machine.py                  состояния, допустимые переходы, гейты
      milestone.py                петля IMPLEMENT → VERIFY → EVALUATE → FIX
      escalation.py               tiers, circuit breaker, no-progress
    runners/
      base.py                     AgentRunner, AgentRequest, AgentResult
      claude_cli.py               основной адаптер
      claude_sdk.py               (позже)
      mock.py
      permissions.py              генерация settings/allowlist по роли
    prompts/builder.py            сборка промпта из agents/*.md + артефактов (string.Template)
    state/
      events.py                   append-only журнал, fsync, чтение с отбрасыванием битого хвоста
      projection.py               свёртка событий в state.json
      lock.py
    gitops/checkpoints.py
    verify/
      godot.py                    GodotRunner (таймаут, лог, классификация ошибок)
      scenarios.py                исполнение и утверждения
      web.py                      экспорт, HTTP-сервер, Playwright
      screenshots.py
      evidence.py                 сборка evidence-пакета, свежесть, SHA
    routing/models.py, routing/budget.py
    platform/proc.py, platform/display.py   завершение процессов, Xvfb/окно — платформенно-зависимое здесь
    yandex/                       requirements/*.yaml (с source_url), validators/, sdk/ (mock-SDK для Playwright), release/
  agents/                         planner.md, engineer.md, evaluator.md, debugger.md, release_writer.md, final_gate.md
  schemas/                        JSON Schema: plan, acceptance, contract, eval_report, release_meta
  templates/godot/                шаблон игры (раздел 5.1)
  config/                         harness.yaml, models.yaml (+ *.example)
  tests/                          unit + интеграционные на mock-раннере
  docs/
```

Требование Yandex в YAML (формат для этапа 7):

```yaml
- id: sound_pauses_on_focus_loss
  section: "1.3"
  source_url: https://yandex.com/dev/games/doc/en/concepts/requirements
  verified_at: null              # заполняется после сверки с первоисточником
  severity: critical
  validation: automatic          # automatic | assisted | manual
  validator: web.focus_audio
```

## 9. CLI

```bash
harness doctor                              # Python, git, claude (версия, аутентификация), godot (версия = пину), export templates, Xvfb, Playwright, sandbox
harness create "<идея>" [--name game_001]   # workspace + repo из шаблона + первый коммит; без LLM
harness run game_001 [--until prototype]    # идемпотентно: продолжает с последнего согласованного состояния
harness resume game_001                     # алиас run
harness status game_001                     # состояние, веха, попытка/tier, бюджет, последний чекпоинт, открытые issues
harness test game_001 [--web] [--scenario X]# только проверки, без агентов
harness approve | revise "<текст>" | stop game_001
harness rollback game_001 --to cp/prototype
harness audit game_001                      # Yandex-аудит
harness release game_001
harness logs game_001 [--role engineer] [--run r-0031] [--follow]
harness budget game_001 --add 10            # продлить бюджет после PAUSED
```

---

## 10. План MVP по этапам

Каждый этап заканчивается работающей системой и коммитом. Итеративно: сделать → запустить → проверить → закоммитить.

| Этап | Содержание | Критерий готовности |
|---|---|---|
| **0. Окружение** | `pyproject` (uv), каркас пакета, `harness doctor` | `harness doctor` зелёный на Linux; на Windows зелёный с понятными предупреждениями (нет Xvfb/sandbox) |
| **2. MVP-0: скелет** | CLI `create/run/status/logs`, журнал событий + проекция, lock, `AgentRunner` + `claude_cli` + `mock`, per-run settings/права, Git-чекпоинты, минимальный шаблон Godot, роль Engineer (идея → проект) | `harness create "simple 2D platformer"` + `harness run game_001` дают проект, который проходит `--headless --import` и `--check-only` без ошибок; в журнале шаги, модель, стоимость; есть тег чекпоинта. **Тесты Harness на mock-раннере**: убийство процесса посреди шага и повторный `run` корректно продолжают |
| **3. MVP-1: проверка игры** | шаблон с `addons/harness` (probe, runner), Game Contract (ручной на этом этапе), сценарии, smoke, скриншоты (Xvfb/окно), отчёт `reports/<sha>/` | `harness test game_001` выдаёт JSON + человекочитаемый отчёт: результаты сценариев, ошибки движка, скриншоты; проваленный сценарий даёт понятное воспроизведение |
| **4. Web-сборка** | пресеты Web/Web-Test, HTTP-сервер, Playwright: загрузка, консоль, мобильная эмуляция, тач, фокус/аудио, reload; Platform-слой с mock-бэкендом | прототип собирается и запускается в Chromium без ошибок консоли; отчёт содержит скриншоты десктоп/мобильный и результат проверки фокуса |
| **5. Цикл Planner → Engineer ⇄ Evaluator** | Planner (GDD, ACCEPTANCE, CONTRACT, PLAN через `--json-schema`), вехи, Evaluator со схемой отчёта, FIX-петля, эскалация, бюджеты, circuit breaker, `rollback`, калибровка моделей | система сама доводит простую игру до PASS или останавливается с понятной причиной; после `kill -9` посреди Engineer'а `harness run` продолжает с того же места; калибровка 6.3 проведена, дефолты моделей обновлены |
| **6. Гейт человека и ассеты** | HUMAN_REVIEW (`approve/revise/stop`), уровни ассетов 0–1, манифест и валидатор лицензий, (опционально) уровень 2 | игра проходит путь без серых прямоугольников; гейт включается/выключается конфигом; манифест полон |
| **7. Yandex-слой** | сверка требований с официальной документацией, `requirements/*.yaml` с `source_url`, мост к SDK через JavaScriptBridge, mock-SDK, валидаторы (архив, размер, имена, LoadingAPI, пауза на рекламе/фокусе, сохранения, язык, ссылки) | `harness audit game_001` выдаёт отчёт по каждому требованию: pass / fail / manual, со ссылкой на источник |
| **8. Релиз и финальный гейт** | Release writer, иконка/обложка/скриншоты нужных размеров, zip, Final gate | `harness release game_001` выдаёт архив и метаданные; Final gate даёт READY или FAILED с причинами; все вердикты привязаны к HEAD |
| **9. Укрепление и open-source** | Docker-изоляция (runner-протокол по образцу GameForge), тесты восстановления (обрыв, сбой Godot, кривой JSON, сеть), облегчённый export template, README, LICENSE, CONTRIBUTING, `.env.example` | тесты восстановления зелёные в CI; новый пользователь по README доходит до первой игры |

(Этап 1 — этот документ.)

---

## 11. Риски

- **Стоимость и длительность.** Ориентир Anthropic: полный harness на сложном приложении — часы и $100–200. Наши игры проще, но без калибровки это гадание. Защита: бюджеты, `--max-budget-usd` на каждый запуск, PAUSED вместо молчаливого перерасхода.
- **Подгонка под тесты.** Защита: приёмочные сценарии вне repo, read-only `addons/harness` с проверкой хэша, Evaluator не видит рассуждений Engineer'а.
- **GDScript-ошибки.** Защита: строгая типизация как ошибки, `--check-only`, раздел ловушек в гайде, быстрый детерминированный VERIFY.
- **Программный рендер.** Медленный и может отличаться от реального WebGL в мелочах. Видео необязательно; финальную визуальную проверку делать в Web-сборке.
- **Требования Yandex меняются.** Каждое требование с источником и датой сверки; аудит показывает устаревшие.
- **Изменения Claude Code CLI.** Весь разбор потока в одном адаптере, `doctor` проверяет минимальную версию, mock-тесты фиксируют формат.

## 12. Открытые вопросы к вам

1. **Аутентификация.** Запускаем Claude Code по подписке (OAuth-токен) или по `ANTHROPIC_API_KEY`? От этого зависит изоляция (`--bare` только с API-ключом) и смысл бюджетов в долларах.
2. **Бюджет на одну игру.** Устраивает ориентир $40 на проект / $12 на веху как стартовый (до калибровки)?
3. **Версия Godot.** Пиним 4.7.2 (текущая стабильная)? Кастомный облегчённый export template для уменьшения размера Web-сборки делаем на этапе 9 или раньше?
4. **Где запускается.** Linux-сервер без GPU (тогда Xvfb + программный рендер) или с GPU? Насколько важна полная работоспособность на Windows-ноутбуке: все этапы или только разработка Harness?
5. **Ассеты.** Достаточно для MVP уровней 0–1 (процедурные + CC0), или сразу нужна платная генерация? Если нужна, какой провайдер и лимит?
6. **Языки игр.** Только русский или русский + английский? (Нужно сверить с требованиями Yandex.)
7. **Гейт человека после прототипа** включён по умолчанию — подтверждаете?
8. **Доступ к документации Yandex.** В этом облачном окружении `yandex.ru` и `yandex.com` заблокированы сетевой политикой. Для этапа 7 нужно добавить их в Allowed domains окружения или выполнять сверку требований на вашей машине.
9. **Yandex SDK-мост.** Пишем свой тонкий мост (рекомендую; сторонние аддоны только как reference) или вендорим готовый аддон (нужна проверка лицензии)?
10. **Жанры MVP.** Ограничиваемся 2D (рекомендую: дешевле, лучше работает в Web и на программном рендере)?
11. **Лицензия Harness.** MIT или Apache-2.0?

---

## 13. Источники

Референсные проекты (исходники прочитаны):
- Godogen — https://github.com/htdt/godogen (`prompts/runtime.md`, `engines/godot.md`, `asset-gen/SKILL.md`, `CHANGELOG.md`, `docs/gdscript-vs-csharp.md`)
- GameForge-Harness — https://github.com/AlbusChen/GameForge-Harness (`docs/architecture.md`, `docs/ISOLATION_RUNNER_PROTOCOL.md`, `gameforge/orchestrator/*`, `gameforge/adapters/godot_harness.py`, `gameforge/resources/godot/fixed_camera_capture.gd`, `experiments/assets/open_game_probe.gd`, `showcases/*/input-trace.json`)
- godot-gamestudio — https://github.com/schmoenraad/godot-gamestudio (`skills/godot-gamestudio/SKILL.md`, `references/state-and-gates.md`, `references/verification-backends.md`, `references/role-routing.md`, `scripts/studio_state.py`, `scripts/godot_guard.py`, `docs/benchmark-results/2026-06-14-pilot.md`)

Claude Code и модели:
- Headless / Agent SDK CLI — https://code.claude.com/docs/en/headless
- CLI reference — https://code.claude.com/docs/en/cli-reference
- Agent SDK Python — https://code.claude.com/docs/en/agent-sdk/python
- Cost tracking — https://code.claude.com/docs/en/agent-sdk/cost-tracking
- Sandboxing — https://code.claude.com/docs/en/sandboxing
- Permissions — https://code.claude.com/docs/en/permissions
- Models overview — https://platform.claude.com/docs/en/about-claude/models/overview
- Pricing — https://platform.claude.com/docs/en/about-claude/pricing

Практики harness:
- https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- https://www.anthropic.com/engineering/harness-design-long-running-apps
- GameCraft-Bench — https://arxiv.org/abs/2606.17861
- GameDevBench — https://arxiv.org/abs/2602.11103
- Godot MCP: https://github.com/Erodenn/godot-mcp-runtime, https://github.com/hybridindie/godot-mcp, https://github.com/satelliteoflove/godot-mcp

Godot:
- Exporting for the Web — https://github.com/godotengine/godot-docs/blob/master/tutorials/export/exporting_for_web.rst
- JavaScriptBridge — https://github.com/godotengine/godot-docs/blob/master/tutorials/platform/web/javascript_bridge.rst
- Command line — https://github.com/godotengine/godot-docs/blob/master/tutorials/editor/command_line_tutorial.rst
- Релизы — https://github.com/godotengine/godot/tags (последний стабильный тег 4.7.2-stable)
- C# и Web — https://github.com/godotengine/godot/issues/70796

Yandex Games (через поиск; напрямую в этом окружении недоступно, требует сверки):
- Требования — https://yandex.com/dev/games/doc/en/concepts/requirements
- 1.9 — https://yandex.com/dev/games/doc/en/requirements/1/9 ; 1.14 — https://yandex.com/dev/games/doc/en/requirements/1/14 ; 1.19 — https://yandex.com/dev/games/doc/en/requirements/1/19 ; 4.4 — https://yandex.com/dev/games/doc/en/requirements/4/4 ; 8.3.5 — https://yandex.com/dev/games/doc/en/requirements/8/3/5 ; 8.3.6 — https://yandex.com/dev/games/doc/en/requirements/8/3/6
- SDK: события игры — https://yandex.com/dev/games/doc/en/sdk/sdk-game-events ; реклама — https://yandex.com/dev/games/doc/en/sdk/sdk-adv ; игрок/сохранения — https://yandex.com/dev/games/doc/en/sdk/sdk-player ; окружение — https://yandex.com/dev/games/doc/en/sdk/sdk-environment
- Черновик игры — https://yandex.com/dev/games/doc/en/console/add-new-game/draft
- Reference-реализации SDK: https://github.com/indiesoftby/defold-yagames , https://github.com/ineedmypills/YandexGamesSDK4Godot , https://github.com/talkafk/WebBus
