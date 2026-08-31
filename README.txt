Rambler IMAP CLI

CLI загружает подготовленные .eml-письма в INBOX Rambler. Загрузка всегда
требует явного выбора: сабсет, одно письмо или все доступные письма.

Структура:
- upload_all_to_rambler.sh        совместимый launcher
- rambler_imap_cli.py             CLI-приложение
- BRIEF_DAY_TESTING.md            короткий маршрут прогона Brief Day
- messages/subsets/<name>/        сценарные наборы
- messages/sent/                  копии успешно загруженных писем
- upload.log                      журнал загрузки

Подготовка:
  chmod +x upload_all_to_rambler.sh rambler_imap_cli.py

Один раз сохранить логин и пароль приложения после успешного входа:
  ./upload_all_to_rambler.sh --remember-credentials --login mikhail.kozyrev@rambler.ru --subset smoke_core

После этого можно грузить сколько угодно сабсетов без повторного ввода пароля:
  ./upload_all_to_rambler.sh --subset prioritization_deadlines --subset deadline_memory_day_1
  ./upload_all_to_rambler.sh --file messages/subsets/calendar_candidates_min/001_train_moscow_novgorod.eml

Сбросить сохранённые данные:
  ./upload_all_to_rambler.sh --clear-credentials

Альтернатива без сохранения файла:
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

После успешной загрузки исходный .eml остается в сабсете. В messages/sent/
сохраняется копия фактически отправленного письма. Если имя уже занято, CLI
добавляет к копии уникальный суффикс.

CLI рекурсивно ищет письма внутри выбранного сабсета. MIME-вложения, уже
встроенные в .eml, передаются в IMAP APPEND вместе с письмом.

Формат upload.log:
  UTC-дата<TAB>имя файла<TAB>tracking_id<TAB>Message-ID<TAB>статус<TAB>детали
