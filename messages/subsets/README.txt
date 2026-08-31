Каждая дочерняя папка — отдельный сабсет писем. Складывайте .eml-файлы
непосредственно в нужную папку и выбирайте её через --subset NAME.

Большие сценарные наборы:
- baseline_profile: портрет, школа, доставки, ЖКХ, офисные и фоновые письма.
- prioritization_deadlines: срочные дедлайны сегодня/завтра и 3-7 дней.
- deadline_memory_near_due: нерелевантная почта для проверки всплытия уже сохранённого дедлайна.
- empty_calendar_candidate: широкий набор писем-кандидатов для календаря.
- attachment_without_drive: письма со встроенными PDF-вложениями и drive.json connected=false.
- travel: поездки, билеты, отели, страховки.
- jobseeker: вакансии, рекрутеры, hh/LinkedIn/Getmatch.
- feedback_category_rules: Ozon, СДЭК, Wildberries, аптеки, банк.
- promo_flood: рекламный шум.

Точечные сабсеты для быстрого прогона Brief Day:
- smoke_core: договор на согласование + OTP-код.
- info_only: одна информационная рассылка без дедлайна и действия.
- deadline_memory_day_1: дальний дедлайн ОСАГО на 13 августа 2026.
- calendar_candidates_min: два события-кандидата и пустой calendar.json.
- calendar_empty_no_candidate: пустой календарь и письмо без события.

Актуальные acceptance-сабсеты:
- acceptance_profile_signals: 4 персональных сигнала поиска работы/ML без рекламного шума.
- acceptance_deadlines: дедлайны сегодня, завтра, через 5 и через 10 дней.
- acceptance_feedback: Ozon + СДЭК + школьное контрольное письмо.
- acceptance_attachment: одно письмо с реальным встроенным PDF-вложением.
- acceptance_auto_interest_mixed: 5 личных сигналов интереса к автомобилю среди промо-шума.

Для основного ручного прогона используй только точечные и acceptance-сабсеты.
Большие наборы выше сохранены для расширенной регрессии и диагностики; они
намеренно не входят в быстрый release-check.
