# File Inventory Tool

Модульний консольний інструмент для інвентаризації документів із простим меню та безпечними типовими налаштуваннями. Запуск:

```bash
python -m app.main
```

На Windows можна скористатися `run.bat`, який автоматично активує `.venv` та виставляє UTF-8 для консолі.

## Основні можливості
- Рекурсивне сканування каталогу, збір метаданих та SHA-256.
- Визначення точних дублікатів (size → SHA-256) з карантином або видаленням до кошика.
- Витяг тексту з TXT/CSV/DOCX/PDF (з OCR за наявності Tesseract) та базова класифікація.
- Генерація єдиного реєстру `inventory.xlsx` + `inventory.xls` з аркушами-поданнями та `run_summary`.
- Атомарні перейменування за шаблоном, опційне фізичне сортування по підпапках.
- Журнали у форматах `log_readable.txt` та `log_events.jsonl`, контроль прогресу та ETA.
- Збереження конфігурації у `runs/<час>/config.yaml` і `%APPDATA%/FileInventoryTool/config.yaml`.

## Інсталяція залежностей
Перед першим запуском утворюється `.venv`. Усі залежності перелічено в `requirements.txt`. Для ручної інсталяції:

```bash
# Linux/macOS
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m app.main

# Windows (PowerShell)
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install --upgrade pip
pip install -r requirements.txt
python -m app.main
```

> **Примітка.** Якщо не активуєте `.venv`, виконайте команди напряму:
>
> ```powershell
> .\.venv\Scripts\python.exe -m pip install --upgrade pip
> .\.venv\Scripts\python.exe -m pip install -r requirements.txt
> .\.venv\Scripts\python.exe -m app.main
> ```

## Побудова .exe
Скрипт `build_win.bat` встановлює необхідні пакети й запускає PyInstaller у режимі `--onefile`.

## Повна специфікація

```
РОЛЬ: старший інженер. МЕТА: створити модульний консольний інструмент для Windows (сумісний із Linux/macOS), орієнтований на недосвідчених користувачів, з текстовим меню й безпечними дефолтами. Інструмент рекурсивно аналізує файли, витягає текст (DOCX/PDF/зображення з OCR), виявляє дублікати/«майже дублікати» не за назвою, а за вмістом, перейменовує за чітким шаблоном, за бажанням фізично сортує по підпапках, веде зрозумілі журнали й формує єдиний реєстр ТІЛЬКИ у форматах Excel (.xlsx і додатково сумісний .xls). Уся консоль і файли журналів — у Windows-UTF-8 без «кракозябр».

1) СТРУКТУРА ПРОЄКТУ (створити файли)
- app/
  - main.py            # вхідна точка, TUI-меню (цифрами), майстер першого запуску
  - deps.py            # перевірка/встановлення залежностей; авто-venv; перезапуск у .venv
  - scan.py            # обхід ФС, збирання метаданих, довгі шляхи (\\?\), потокове читання
  - dedup.py           # пошук ДУБЛІКАТІВ: grouping by size → SHA-256; «майже дублікати»: SimHash/pHash
  - extract.py         # витяг тексту: txt/docx/pdf; визначення потреби OCR; обгортка pytesseract
  - classify.py        # базові евристики (категорія/дата з контенту); опційний LLM (вимкнено за замовч.)
  - rename.py          # шаблон імені; нормалізація/транслітерація; інкремент версії; атомарне перейменування
  - sortout.py         # фізичне сортування у _sorted та «карантин» для дублікатів/_near_duplicates
  - inventory.py       # генерація Excel (.xlsx + .xls), аркуші, стилі/формати, оновлення подань
  - loggingx.py        # log_readable.txt (людською), log_events.jsonl (структурований)
  - progress.py        # зважений прогрес+ETA; мікропрогрес активного файла
  - config.py          # читання/запис YAML; дефолти; category_map; знімок effective_config
- run.bat              # запуск у Windows із UTF-8 (chcp 65001) і викликом python у .venv
- requirements.txt     # перелік залежностей
- README_UA.md         # коротка інструкція користувача
- build_win.bat        # (необов’язково) збірка .exe через PyInstaller --onefile

2) ЗАЛЕЖНОСТІ (АВТОВСТАНОВЛЕННЯ НА СТАРТІ)
- обов’язкові: pydantic, loguru, chardet, unidecode, colorama, rich
- Excel: pandas, openpyxl (обов’язково для .xlsx), xlwt (для сумісного .xls)
- Документи/зображення: python-docx, pdfminer.six (або pypdf), pillow
- OCR: pytesseract (перевіряти наявність «tesseract» у PATH; якщо нема — запропонувати інструкцію/продовжити без OCR)
- LLM (опційно, за вимкненим дефолтом): requests (або офіційний клієнт)
- Безпечне видалення у кошик: send2trash
Поведінка deps.ensure_ready():
  • якщо процес не у .venv → створити .venv, встановити туди залежності, перезапустити себе в .venv без участі користувача;
  • для Windows увімкнути UTF-8: SetConsoleCP/SetConsoleOutputCP(65001); sys.stdout/sys.stderr → encoding='utf-8';
  • відсутні пакети встановлювати через pip із прогресом; повідомлення — українською.

3) TUI-МЕНЮ (точна поведінка)
[1] Швидкий аналіз (dry-run)  
   — запитати ROOT, показати ключові параметри (OCR, LLM, потоки, шаблон).  
   — ПОРЯДОК ЕТАПІВ: (а) «дублікати спочатку»: групування за розміром → SHA-256; опційно «майже дублікати»; (б) витяг/ОCR; (в) евристики/LLM; (г) розрахунок нового імені; (ґ) формування Excel+журнали.  
   — нічого на диску не змінювати.  
[2] Застосувати перейменування (commit)  
   — попередження і просте підтвердження: «Виконати перейменування? [Y/n]» (Y = дія).  
   — після підтвердження поставити додаткові запитання:  
      • «Видаляти точні дублікати замість карантину? [Y/n]» (якщо Y — send2trash; якщо n — карантин)  
      • «Сортувати файли по підпапках? [Y/n]» → якщо Y: 1=by_category, 2=by_date, 3=by_type.  
   — виконати: перейменування → обрана стратегія з дублікатами → (за потреби) фізичне сортування → оновити Excel/журнали.  
[3] Переглянути підсумок останнього запуску  
   — короткі метрики; пропозиція відкрити inventory.xlsx.  
[4] Налаштування  
   — вибір папки, OCR (ukr+eng/eng/off), LLM (off/on), потоки, ШАБЛОН, category_map, політика дублікатів, сортування.  
[5] Відновити незавершений запуск  
   — progress.json → продовжити/почати заново.  
[6] Сортування та подання  
   — перегенерувати подання в Excel; опційно фізично розкласти у _sorted (тільки за підтвердженням).  
[7] Перевірити/переінсталювати залежності  
[8] Вихід

4) АНАЛІТИКА ВМІСТУ ТА ДЕДУПЛІКАЦІЯ
- ТОЧНІ дублікати: попередньо групувати файли за size_mb (або size_bytes внутрішньо), у групах рахувати потоковий SHA-256; однакові хеші → одна група. Вибирати «канонічний» V1 за правилом: найраніший mtime → найкоротший шлях. Решті присвоювати V2…Vn.  
- «МАЙЖЕ» дублікати (опційно):  
  • текстові (DOCX/текстові PDF): нормалізований текст → SimHash; поріг схожості за Hamming;  
  • зображення/скани: перцептивний pHash.  
- ПОВЕДІНКА в commit:  
  • за замовчуванням нічого не видаляти; точні дублікати переміщати в «карантин» `<root>/_duplicates/<dup_group_id>/…` з суфіксами `_dupV0N_[hash8]`; «майже» — до `<root>/_near_duplicates/<group_id>/…` з `_nDupS{score}_V0N_[hash8]`.  
  • якщо користувач обрав «видаляти точні дублікати» — send2trash для V2…Vn; V1 лишається.  
  • V1 (канонічний) і всі «не дублікати» далі перейменовуються за шаблоном, а потім (за згодою) фізично сортуються.

5) ШАБЛОН ПЕРЕЙМЕНУВАННЯ (за замовчуванням)
`{category}_{yyyy}-{mm}-{dd}_{short_title}_v{version:02d}_[{hash8}]{ext}`  
- `category` — із контрольованої таксономії (editable через config; дефолт: договір, рахунок, акт, протокол, лист, наказ, звіт, кошторис, тендер, презентація, довідка, ТЗ, специфікація, інше).  
- `date` — з контенту/метаданих; якщо не знайдено — mtime.  
- `short_title` — нормалізований фрагмент заголовка, транслітерований, без заборонених символів, довжина ≤ 50.  
- `version` — v01, v02… (інкремент при колізіях).  
- `hash8` — перші 8 символів SHA-256 вмісту.  
- Імена застосовувати у детермінованому алфавітному порядку нових назов.

6) СОРТУВАННЯ ПО ПІДПАПКАХ (за бажанням користувача у commit)
- Структури призначення:  
  • by_category: `<root>/_sorted/by_category/<Категорія>/…`  
  • by_date: `<root>/_sorted/by_date/<YYYY>/<YYYY-MM>/…`  
  • by_type: `<root>/_sorted/by_type/<ext>/…`  
- Після будь-яких переміщень ОБОВ’ЯЗКОВО перегенерувати Excel (всі аркуші) із фактичними фінальними шляхами.

7) ПРОГРЕС І ЖУРНАЛИ
- Прогрес: зважені одиниці роботи (метадані, дедуп, витяг/ОCR, аналіз/LLM, перейменування, інвентар/Excel), загальний % і ETA; мікропрогрес активного файла; індикатор «indeterminate» для невідомих підетапів до визначення.  
- Журнали в runs/<ISO8601>/:  
  • log_readable.txt — короткі повідомлення українською («Витяг тексту…», «Перейменовано…», «У карантин: dupV02…», «Видалено дублікати: N»).  
  • log_events.jsonl — об’єкти: {run_id, ts, category, file_id (sha256_8), path_old, path_new, path_final, stage, status, duration_ms, text_len, tokens_in/out, cost_usd, confidence, quality, dup_type, dup_group_id, dup_rank, lifecycle_state, message}.  
- Маскувати у читабельному журналі ПІБ/адреси/номери; у JSONL — хеш-замінники.

8) EXCEL (ОБОВ’ЯЗКОВО .xlsx + дубль .xls; без CSV за замовчуванням)
- Аркуш "inventory" (мінімально достатні колонки, порядком нижче; кодування Unicode; для `size_mb` стиль `0.00`):
  root; folder_old; path_old; name_old;  
  name_new; folder_new; path_new;  
  sorted (bool); sort_strategy; sorted_subfolder; path_final;  
  ext; mime; size_mb (дві десяткові);  
  ctime (datetime); mtime (datetime); date_doc (date);  
  category; short_title; version; hash8;  
  content_hash_sha256; dup_type (unique|exact_dup|near_dup); dup_group_id; dup_rank (V1/V2…); dup_master_path; near_dup_score;  
  lifecycle_state (present|quarantined|deleted); deleted_ts (datetime, якщо було видалення);  
  text_source (parser|ocr); ocr_lang; text_len; extract_quality;  
  llm_used (bool); llm_confidence; llm_keywords; summary_200;  
  rename_status (success|skipped|failed); error_message; collision (bool); duration_s; mode (dry-run|commit).
- Аркуші-подання (ті самі колонки):  
  "by_category" (сортування за category),  
  "by_date" (за date_doc; додати допоміжні `year`, `year_month`),  
  "by_type" (за ext/mime).  
- Аркуш "run_summary" (один рядок агрегатів):  
  run_id; files_total; files_processed; renamed_ok; renamed_failed;  
  duplicate_groups; duplicate_files; near_duplicate_files; quarantined_count; deleted_count;  
  ocr_share; llm_share; collisions; duration_total_s; cost_total_usd;  
  total_size_mb; sorted_enabled; sorting_strategy; moved_count; sorted_root ("_sorted"); excel_updated (true).

9) БЕЗПЕКА/НАДІЙНІСТЬ
- За замовчуванням: dry-run; LLM — off.  
- Windows довгі шляхи: префікс `\\?\`; зрозумілі повідомлення.  
- Ctrl+C: м’яке завершення, синхронізація журналів та Excel, збереження progress.json.  
- Переміщення (move) за замовчуванням; якщо сортування на інший диск — спочатку копія з перевіркою SHA-256, потім видалення джерела.  
- Видалення (лише для точних дублікатів і лише за згодою): через send2trash з фіксацією `lifecycle_state=deleted` і `deleted_ts`.

10) ДЕФОЛТИ КОНФІГУРАЦІЇ (config.yaml; показати/редагувати в меню)
- rename_template: "{category}_{yyyy}-{mm}-{dd}_{short_title}_v{version:02d}_[{hash8}]{ext}"
- category_map: базовий набір (договір, рахунок, акт, протокол, лист, наказ, звіт, кошторис, тендер, презентація, довідка, ТЗ, специфікація, інше)
- dedup: exact=true (за замовч.), near=false (увімкнути в налаштуваннях), near_threshold (напр., 0.85)
- duplicates_policy: exact="quarantine" | "delete_to_trash" (за замовч. quarantine), near="quarantine"
- export_mode: "views_only" | "physical_sort" (за замовч. запитувати кожного разу)
- sorted_targets: ["by_category","by_date","by_type"]; sorted_root: "_sorted"
- ocr_lang: "ukr+eng"; llm: off; threads: auto

ОЧІКУВАНИЙ РЕЗУЛЬТАТ
- Користувач запускає app/main.py (або .exe), бачить меню 1–8.  
- При першому запуску інструмент сам створює .venv, встановлює залежності та перемикає консоль на UTF-8.  
- [1] dry-run: знаходить дублікати/майже дублікати, оцінює імена, генерує runs/<час>/ з: log_readable.txt, log_events.jsonl, progress.json, inventory.xlsx, inventory.xls.  
- [2] commit: просте підтвердження `Y`; за вибором — видалення точних дублікатів у кошик чи карантин; опціональне сортування в _sorted; Excel перегенеровано, `path_final` відображає фактичне розташування; аркуші-подання та підсумки оновлені.
```

