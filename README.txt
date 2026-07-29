Rambler IMAP CLI

CLI загружает подготовленные .eml-письма в INBOX Rambler. Загрузка всегда
требует явного выбора: сабсет, одно письмо или все доступные письма.

Структура:
- upload_all_to_rambler.sh        совместимый launcher
- rambler_imap_cli.py             CLI-приложение
- messages/subsets/<name>/        сценарные наборы
- messages/sent/                  успешно загруженные письма
- upload.log                      журнал загрузки

Подготовка:
  chmod +x upload_all_to_rambler.sh rambler_imap_cli.py
  export RAMBLER_LOGIN="user@rambler.ru"
  export RAMBLER_PASSWORD="пароль приложения"

Команды:
  ./upload_all_to_rambler.sh --list-subsets
  ./upload_all_to_rambler.sh --subset baseline_profile --dry-run
  ./upload_all_to_rambler.sh --subset baseline_profile
  ./upload_all_to_rambler.sh --subset travel --subset promo_flood
  ./upload_all_to_rambler.sh --file messages/subsets/travel/001_message.eml
  ./upload_all_to_rambler.sh --all
  ./upload_all_to_rambler.sh --help

Локальная проверка без IMAP:
  python3 -m unittest -v

Если для --file передано только имя, CLI ищет его во всех доступных исходных
письмах. При совпадении нескольких файлов нужно передать точный путь.

Сценарные наборы:
- baseline_profile
- prioritization_deadlines
- deadline_memory_day_1
- deadline_memory_near_due
- empty_calendar_candidate
- attachment_without_drive
- travel
- jobseeker
- feedback_category_rules
- promo_flood

После успешной загрузки исходный .eml остается в сабсете. В messages/sent/
сохраняется копия фактически отправленного письма. Если имя уже занято, CLI
добавляет к копии уникальный суффикс.

CLI рекурсивно ищет письма внутри выбранного сабсета. MIME-вложения, уже
встроенные в .eml, передаются в IMAP APPEND вместе с письмом. Это позволяет
загружать attachment_without_drive/messages/ вместе с PDF-документами.

Формат upload.log:
  UTC-дата<TAB>имя файла<TAB>tracking_id<TAB>Message-ID<TAB>статус<TAB>детали
