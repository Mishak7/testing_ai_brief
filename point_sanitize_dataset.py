#!/usr/bin/env python3
"""Point-edit EML text and headers while preserving MIME attachments."""

from __future__ import annotations

import argparse
import csv
import hashlib
import html
import json
import re
import unicodedata
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from email import encoders, policy
from email.message import Message
from email.parser import BytesParser
from email.utils import format_datetime, getaddresses, parsedate_to_datetime
from html.parser import HTMLParser
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import pdfplumber
from reportlab.lib.colors import HexColor
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen.canvas import Canvas


TARGET_EMAIL = "mikhail.kozyrev@rambler.ru"
TARGET_DISPLAY_NAME = "Михаил Козырев"
TARGET_FULL_NAME = "Козырев Михаил Андреевич"
MSK = timezone(timedelta(hours=3))
FONT_PATH = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")

SUBSET_SCHEDULE = {
    "baseline_profile": (datetime(2026, 7, 29, 8, 0, tzinfo=MSK), 90),
    "prioritization_deadlines": (datetime(2026, 7, 29, 8, 15, tzinfo=MSK), 30),
    "deadline_memory_day_1": (datetime(2026, 7, 29, 9, 0, tzinfo=MSK), 35),
    "deadline_memory_near_due": (datetime(2026, 8, 3, 8, 0, tzinfo=MSK), 35),
    "empty_calendar_candidate": (datetime(2026, 7, 29, 9, 30, tzinfo=MSK), 75),
    "attachment_without_drive": (datetime(2026, 7, 29, 10, 0, tzinfo=MSK), 100),
    "travel": (datetime(2026, 7, 29, 8, 30, tzinfo=MSK), 110),
    "jobseeker": (datetime(2026, 7, 29, 9, 15, tzinfo=MSK), 95),
    "feedback_category_rules": (datetime(2026, 7, 29, 8, 45, tzinfo=MSK), 55),
    "promo_flood": (datetime(2026, 7, 29, 7, 30, tzinfo=MSK), 17),
}

MONTHS_RU = {
    "января": 1, "февраля": 2, "марта": 3, "апреля": 4,
    "мая": 5, "июня": 6, "июля": 7, "августа": 8,
    "сентября": 9, "октября": 10, "ноября": 11, "декабря": 12,
}
MONTHS_RU_REV = {value: key for key, value in MONTHS_RU.items()}
MONTHS_EN = {
    "january": 1, "february": 2, "march": 3, "april": 4,
    "may": 5, "june": 6, "july": 7, "august": 8,
    "september": 9, "october": 10, "november": 11, "december": 12,
}
MONTHS_EN_REV = {value: key.title() for key, value in MONTHS_EN.items()}

SAFE_ADDRESSES = [
    "Санкт-Петербург, проспект Энергетиков, дом 25, квартира 48",
    "Санкт-Петербург, улица Оптиков, дом 18, квартира 32",
    "Санкт-Петербург, набережная Реки Карповки, дом 31, квартира 14",
]

TRANSPORT_HEADERS = {
    "received", "return-path", "delivered-to", "envelope-to", "x-original-to",
    "authentication-results",
    "dkim-signature", "domainkey-signature", "arc-authentication-results",
    "arc-message-signature", "arc-seal", "x-google-smtp-source",
    "x-received", "x-yandex-spam", "x-yandex-front", "x-yandex-time-mark",
    "dkim-filter", "feedback-id", "thread-index",
    "require-recipient-valid-since",
}

SAFE_PRODUCTS = [
    "Настольная лампа LED",
    "Органайзер для документов",
    "Набор контейнеров для кухни",
    "Комплект кухонных полотенец",
    "Кабель USB-C, 1 метр",
    "Чехол для чемодана",
    "Термокружка 450 мл",
    "Блокнот в твёрдой обложке",
    "Набор батареек AA",
    "Подставка для ноутбука",
    "Фильтр-кувшин для воды",
    "Набор крючков для дома",
    "Зонт складной",
    "Коврик для рабочего стола",
    "Набор салфеток из микрофибры",
    "Контейнер для хранения",
    "Настольный календарь",
]

SAFE_OTHER_PEOPLE = [
    "ПЕТРОВА АННА СЕРГЕЕВНА",
    "СОКОЛОВА ЕЛЕНА ИГОРЕВНА",
    "ОРЛОВ АНТОН ВИКТОРОВИЧ",
]


class Transformer:
    def __init__(self, personal_emails: set[str], product_names: set[str]):
        self.personal_emails = {value.lower() for value in personal_emails if value}
        self.product_map = {
            original: SAFE_PRODUCTS[index % len(SAFE_PRODUCTS)]
            for index, original in enumerate(sorted(product_names))
        }
        self.url_map: dict[str, str] = {}
        self.id_map: dict[str, str] = {}
        self.phone_map: dict[str, str] = {}
        self.address_map: dict[str, str] = {}
        self.person_map: dict[str, str] = {}
        self.counts: Counter[str] = Counter()

    @staticmethod
    def digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8", errors="ignore")).hexdigest()

    def fake_url(self, original: str) -> str:
        if original not in self.url_map:
            parsed = urlparse(original.replace("&amp;", "&"))
            domain = parsed.netloc.lower().split(":", 1)[0]
            labels = [x for x in domain.split(".") if x and x not in {"www", "mail", "email", "links", "link", "cdn", "cdn1", "cdn2"}]
            brand = re.sub(r"[^a-z0-9-]", "", labels[0] if labels else "notice") or "notice"
            self.url_map[original] = f"https://{brand}.example/link/{self.digest(original)[:16]}"
        return self.url_map[original]

    def fake_digits(self, original: str) -> str:
        if original not in self.id_map:
            digits = str(int(self.digest(original), 16))
            length = len(original)
            replacement = ("7" + digits)[:length].ljust(length, "4")
            if replacement == original:
                replacement = ("8" + replacement[1:])[:length]
            self.id_map[original] = replacement
        return self.id_map[original]

    def fake_code(self, original: str) -> str:
        key = f"code:{original}"
        if key not in self.id_map:
            letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
            h = self.digest(key)
            result = []
            for index, char in enumerate(original):
                if char.isdigit():
                    result.append(str(int(h[index % len(h)], 16) % 10))
                elif char.isalpha():
                    result.append(letters[int(h[index % len(h)], 16) % len(letters)])
                else:
                    result.append(char)
            self.id_map[key] = "".join(result)
        return self.id_map[key]

    def fake_phone(self, original: str) -> str:
        if original not in self.phone_map:
            suffix = int(self.digest(original)[:6], 16) % 10000
            self.phone_map[original] = f"+7 (000) 555-{suffix // 100:02d}-{suffix % 100:02d}"
        return self.phone_map[original]

    def fake_address(self, original: str) -> str:
        if original not in self.address_map:
            index = int(self.digest(original)[:4], 16) % len(SAFE_ADDRESSES)
            self.address_map[original] = SAFE_ADDRESSES[index]
        return self.address_map[original]

    @staticmethod
    def mapped_date(old_value: date, old_header: date, new_header: date) -> date:
        if date(2026, 7, 29) <= old_value <= date(2026, 9, 30):
            return old_value
        delta = (old_value - old_header).days
        if 0 <= delta <= 45:
            return new_header + timedelta(days=delta)
        seed = int(hashlib.sha256(old_value.isoformat().encode()).hexdigest()[:4], 16)
        return new_header + timedelta(days=1 + seed % 21)

    def replace_dates(self, text: str, old_header: date, new_header: date) -> str:
        def numeric(match: re.Match[str]) -> str:
            day = int(match.group(1)); separator = match.group(2)
            month = int(match.group(3)); year = int(match.group(4))
            try:
                mapped = self.mapped_date(date(year, month, day), old_header, new_header)
            except ValueError:
                return match.group(0)
            self.counts["dates"] += 1
            return f"{mapped.day:02d}{separator}{mapped.month:02d}{separator}{mapped.year:04d}"

        def iso(match: re.Match[str]) -> str:
            year, month, day = map(int, match.groups())
            try:
                mapped = self.mapped_date(date(year, month, day), old_header, new_header)
            except ValueError:
                return match.group(0)
            self.counts["dates"] += 1
            return mapped.isoformat()

        def russian(match: re.Match[str]) -> str:
            day = int(match.group(1)); month = MONTHS_RU[match.group(2).lower()]
            year = int(match.group(3)) if match.group(3) else old_header.year
            try:
                mapped = self.mapped_date(date(year, month, day), old_header, new_header)
            except ValueError:
                return match.group(0)
            self.counts["dates"] += 1
            year_part = f" {mapped.year}" if match.group(3) else ""
            return f"{mapped.day} {MONTHS_RU_REV[mapped.month]}{year_part}"

        def english(match: re.Match[str]) -> str:
            month = MONTHS_EN[match.group(1).lower()]; day = int(match.group(2)); year = int(match.group(3))
            try:
                mapped = self.mapped_date(date(year, month, day), old_header, new_header)
            except ValueError:
                return match.group(0)
            self.counts["dates"] += 1
            return f"{MONTHS_EN_REV[mapped.month]} {mapped.day}, {mapped.year}"

        text = re.sub(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", iso, text)
        text = re.sub(r"\b(\d{1,2})([./-])(\d{1,2})\2(20\d{2})\b", numeric, text)
        month_names = "|".join(MONTHS_RU)
        text = re.sub(rf"\b(\d{{1,2}})\s+({month_names})(?:\s+(20\d{{2}}))?\b", russian, text, flags=re.IGNORECASE)
        en_names = "|".join(MONTHS_EN)
        text = re.sub(rf"\b({en_names})\s+(\d{{1,2}}),?\s+(20\d{{2}})\b", english, text, flags=re.IGNORECASE)
        text = re.sub(r"\b(?:2023|2024|2025)\b", "2026", text)
        return text

    def transform(self, text: str, old_header: date, new_header: date, *, generated: bool = False) -> str:
        if not text:
            return text

        text = re.sub(
            r"(?i)тестовый\s+документ\s*[—-]\s*не\s+для\s+оплаты",
            "Единый платёжный документ",
            text,
        )

        protected: dict[str, str] = {}
        def protect_data(match: re.Match[str]) -> str:
            key = f"__DATA_URI_{len(protected)}__"
            protected[key] = match.group(0)
            return key
        text = re.sub(r"data:[^\s\"')>]+", protect_data, text, flags=re.IGNORECASE)

        for original, replacement in sorted(
            self.product_map.items(), key=lambda item: len(item[0]), reverse=True
        ):
            new_text = text.replace(original, replacement)
            new_text = new_text.replace(html.escape(original), html.escape(replacement))
            if new_text != text:
                self.counts["ozon_product_names"] += 1
                text = new_text

        # Reuse contextual code mappings learned from the plain-text MIME part
        # when the HTML alternative surrounds the same code with tags.
        for key, replacement in list(self.id_map.items()):
            if key.startswith("code:"):
                text = text.replace(key.removeprefix("code:"), replacement)

        name_replacements = [
            (r"(?i)иванов\s+олег\s+михайлович", TARGET_FULL_NAME),
            (r"(?i)олег\s+михайлович\s+иванов", TARGET_FULL_NAME),
            (r"(?i)олег\s+иванов", TARGET_DISPLAY_NAME),
            (r"(?i)\bмиша\b", "Михаил"),
            (r"(?i)козырев\s+михаил\s+андреевич", TARGET_FULL_NAME),
            (r"(?i)михаил\s+андреевич\s+козырев", TARGET_FULL_NAME),
            (r"(?i)kozyrev\s+mikhail", TARGET_DISPLAY_NAME),
            (r"(?i)\bmikhail\b", TARGET_DISPLAY_NAME),
        ]
        if generated:
            name_replacements.append((r"(?i)\bолег\b", "Михаил"))
        for pattern, replacement in name_replacements:
            text, count = re.subn(pattern, replacement, text)
            self.counts["names"] += count

        def passenger_name(match: re.Match[str]) -> str:
            self.counts["other_people"] += 1
            return match.group(1) + TARGET_DISPLAY_NAME
        text = re.sub(
            r"(?i)(\b(?:имя|пассажир|гость)\s*:\s*)"
            r"[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]+(?:\s+[A-ZА-ЯЁ][A-Za-zА-Яа-яЁё-]+)?"
            r"(?=\s*(?:<|$|\r?\n|Категория\b|Дата\b))",
            passenger_name,
            text,
        )

        def official_name(match: re.Match[str]) -> str:
            value = match.group(0)
            if "КОЗЫРЕВ" in value and "МИХАИЛ" in value:
                return "КОЗЫРЕВ МИХАИЛ АНДРЕЕВИЧ"
            if value not in self.person_map:
                self.person_map[value] = SAFE_OTHER_PEOPLE[len(self.person_map) % len(SAFE_OTHER_PEOPLE)]
            self.counts["other_people"] += 1
            return self.person_map[value]
        text = re.sub(
            r"\b[А-ЯЁ]{2,}\s+[А-ЯЁ]{2,}\s+[А-ЯЁ]{2,}(?:ОВИЧ|ЕВИЧ|ИЧ|ОВНА|ЕВНА|ИЧНА)\b",
            official_name,
            text,
        )

        def replace_email(match: re.Match[str]) -> str:
            value = match.group(0)
            if value.lower() in self.personal_emails or value.lower() == TARGET_EMAIL:
                self.counts["personal_emails"] += value.lower() != TARGET_EMAIL
                return TARGET_EMAIL
            return value
        text = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-zА-Яа-я]{2,}", replace_email, text)

        text = self.replace_dates(text, old_header, new_header)

        def phone(match: re.Match[str]) -> str:
            value = match.group(0)
            normalized = re.sub(r"\D", "", value)
            if value.startswith("+7 (000) 555-") or (
                normalized.startswith("7000555") and len(normalized) == 11
            ):
                return value
            self.counts["phones"] += 1
            return self.fake_phone(value)

        # Phone links can contain an unformatted number that is not repeated in
        # visible text. Replace the link target before the broader phone pass.
        def tel_link(match: re.Match[str]) -> str:
            original = match.group(1)
            fake = re.sub(r"\D", "", self.fake_phone(original))
            self.counts["phones"] += 1
            key = f"__TEL_URI_{len(protected)}__"
            protected[key] = f"tel:+{fake}"
            return key
        text = re.sub(r"(?i)tel:\s*\+?([\d() .-]{2,}\d)", tel_link, text)

        # A leading plus is a strong phone signal and safely covers foreign
        # company/contact numbers. For Russian numbers beginning with 8, require
        # formatting punctuation so an order number is never turned into a phone.
        text = re.sub(r"(?<![\w\d])\+\d[\d() .-]{6,}\d(?!\d)", phone, text)
        text = re.sub(r"(?<!\d)8(?=[\s(.-])(?:[\s().-]*\d){10}(?!\d)", phone, text)

        def ip_address(match: re.Match[str]) -> str:
            original = match.group(0)
            suffix = 1 + int(self.digest(original)[:4], 16) % 253
            self.counts["ip_addresses"] += 1
            return f"192.0.2.{suffix}"
        text = re.sub(
            r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])",
            ip_address,
            text,
        )

        text, count = re.subn(r"(?i)бульвар\s+новаторов(?:[^<\r\n]{0,80})?", lambda m: self.fake_address(m.group(0)), text)
        self.counts["addresses"] += count
        text, count = re.subn(r"(?i)\bшапки\b", "Зеленогорск", text)
        self.counts["addresses"] += count

        street_pattern = re.compile(
            r"(?i)(?:(?:г\.?|город)\s*[А-ЯЁA-Z][^<\r\n,;]{1,30},?\s*)?"
            r"(?:ул\.?|улица|проспект|пр-т|бульвар|бул\.?|шоссе|переулок|наб\.?|набережная)\s+"
            r"[А-ЯЁA-Z][^<\r\n;]{1,80}"
        )
        def address(match: re.Match[str]) -> str:
            self.counts["addresses"] += 1
            return self.fake_address(match.group(0))
        text = street_pattern.sub(address, text)

        def passport(match: re.Match[str]) -> str:
            self.counts["passport_numbers"] += 1
            return f"{match.group(1)}00 00 000001"
        text = re.sub(r"(?i)(паспорт(?:ные данные)?\s*(?:серия|№|номер|:)?\s*)(?:\d[\s-]*){6,12}", passport, text)

        # Short booking, ticket, shipment and access codes need context: unlike
        # generic long IDs, these are often only four or five characters.
        contextual_id = re.compile(
            r"(?i)((?:\b(?:заказ(?:а)?|накладн(?:ой|ая)|отправлени(?:я|е)|брон(?:ь|и|ирования)|"
            r"билет(?:а)?|рейс(?:а)?|поезд(?:а)?|номер\s+брони|pnr|booking|order|трек(?:-номер)?|"
            r"код(?:\s+(?:получения|бронирования|подтверждения))?|pin(?:-код)?|пин(?:-код)?)\b|№)"
            r"[^\w\d]{0,12})((?=[A-ZА-Я0-9-]*\d)[A-ZА-Я0-9-]{3,})"
        )
        def contextual_code(match: re.Match[str]) -> str:
            self.counts["contextual_identifiers"] += 1
            return match.group(1) + self.fake_code(match.group(2))
        text = contextual_id.sub(contextual_code, text)

        # Card/account-like digit groups are changed only when their shape is
        # long enough to be identifying; spacing and punctuation are preserved.
        def grouped_digits(match: re.Match[str]) -> str:
            value = match.group(0)
            self.counts["financial_identifiers"] += 1
            replacement_digits = self.fake_digits(re.sub(r"\D", "", value))
            iterator = iter(replacement_digits)
            return "".join(next(iterator) if char.isdigit() else char for char in value)
        text = re.sub(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)", grouped_digits, text)

        def url(match: re.Match[str]) -> str:
            self.counts["urls"] += 1
            return self.fake_url(match.group(0))
        text = re.sub(r"https?://[^\s<>\"']*[^\s<>\"'.,;:!?)]", url, text, flags=re.IGNORECASE)

        def digits(match: re.Match[str]) -> str:
            self.counts["identifiers"] += 1
            return self.fake_digits(match.group(0))
        text = re.sub(r"(?<!\d)\d{6,}(?!\d)", digits, text)

        def alpha_code(match: re.Match[str]) -> str:
            value = match.group(0)
            if value.upper() in {"UTF-8", "ISO-8859"}:
                return value
            self.counts["transport_codes"] += 1
            return self.fake_code(value)
        text = re.sub(r"\b[A-ZА-Я]{1,3}[- ]?\d{3,6}[A-ZА-Я]?\b", alpha_code, text)

        for key, value in protected.items():
            text = text.replace(key, value)
        return text


def subset_name(root: Path, path: Path) -> str:
    return path.relative_to(root).parts[0]


def new_header_dates(root: Path) -> dict[Path, datetime]:
    result = {}
    for subset_dir in sorted(path for path in root.iterdir() if path.is_dir()):
        start, step = SUBSET_SCHEDULE[subset_dir.name]
        for index, path in enumerate(sorted(subset_dir.rglob("*.eml"))):
            result[path] = start + timedelta(minutes=index * step)
    return result


def collect_personal_emails(root: Path) -> set[str]:
    values = {TARGET_EMAIL}
    for path in root.rglob("*.eml"):
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        for _name, address in getaddresses([str(message.get("To", "")), str(message.get("Delivered-To", ""))]):
            if address:
                values.add(address.lower())
    return values


def collect_ozon_product_names(root: Path) -> set[str]:
    class ProductLinkParser(HTMLParser):
        def __init__(self) -> None:
            super().__init__(convert_charrefs=True)
            self.in_product_link = False
            self.fragments: list[str] = []
            self.values: set[str] = set()

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag.lower() != "a":
                return
            href = dict(attrs).get("href") or ""
            if re.search(r"(?i)ozon.*(?:product|t/)|/product/", href):
                self.in_product_link = True
                self.fragments = []

        def handle_data(self, data: str) -> None:
            if self.in_product_link:
                self.fragments.append(data)

        def handle_endtag(self, tag: str) -> None:
            if tag.lower() == "a" and self.in_product_link:
                value = " ".join(" ".join(self.fragments).split())
                if 3 <= len(value) <= 250:
                    self.values.add(value)
                self.in_product_link = False
                self.fragments = []

    result = set()
    for path in root.rglob("*.eml"):
        if path.name.startswith("generated_"):
            continue
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        identity = f"{message.get('From', '')} {message.get('Subject', '')}"
        if not re.search(r"(?i)ozon|озон", identity):
            continue
        for part in message.walk():
            if part.get_content_type() != "text/html":
                continue
            try:
                parser = ProductLinkParser()
                parser.feed(part.get_content())
            except Exception:
                continue
            result.update(parser.values)
    return result


def safe_old_date(message: Message, fallback: datetime) -> datetime:
    try:
        value = parsedate_to_datetime(str(message.get("Date", "")))
        if value.tzinfo is None:
            value = value.replace(tzinfo=MSK)
        return value.astimezone(MSK)
    except Exception:
        return fallback


def enforce_scenario_dates(relative_path: str, text: str) -> str:
    if relative_path.endswith("empty_calendar_candidate/001_bilet_v_kazan.eml"):
        text = re.sub(r"\b\d{1,2}([./-])\d{1,2}\1(?:2026)\b", lambda m: f"03{m.group(1)}08{m.group(1)}2026", text)
        text = re.sub(r"\b\d{1,2}\s+(?:июля|августа|сентября)\s+2026\b", "3 августа 2026", text, flags=re.IGNORECASE)
        text = text.replace("20:50", "09:10").replace("08:05", "16:45")
    elif relative_path.endswith("empty_calendar_candidate/001_scandinavia_orthopedist.eml"):
        text = re.sub(r"\b\d{1,2}([./-])\d{1,2}\1(?:2026)\b", lambda m: f"03{m.group(1)}08{m.group(1)}2026", text)
        text = re.sub(r"\b\d{1,2}\s+(?:июля|августа|сентября)\s+2026\b", "3 августа 2026", text, flags=re.IGNORECASE)
        text = text.replace("15:30", "10:30")
    return text


def content_hashes(message: Message) -> list[tuple[str, str, str]]:
    result = []
    for index, part in enumerate(message.walk()):
        if part.is_multipart() or part.get_content_maintype() == "text":
            continue
        payload = part.get_payload(decode=True) or b""
        result.append((part.get_content_type(), part.get_filename() or f"part-{index}", hashlib.sha256(payload).hexdigest()))
    return result


def update_text_part(part: Message, new_text: str) -> None:
    subtype = part.get_content_subtype()
    original_cte = (part.get("Content-Transfer-Encoding") or "").lower()
    saved_headers = {
        name: str(part.get(name)) for name in ("Content-ID", "Content-Location", "Content-Disposition")
        if part.get(name) is not None
    }
    params = [(key, value) for key, value in part.get_params(header="Content-Type", failobj=[]) if key not in {part.get_content_type(), "charset"}]
    cte = original_cte if original_cte in {"7bit", "8bit", "base64", "quoted-printable"} else None
    kwargs = {"subtype": subtype, "charset": "utf-8"}
    if cte:
        kwargs["cte"] = cte
    part.set_content(new_text, **kwargs)
    for key, value in params:
        part.set_param(key, value, header="Content-Type")
    for name, value in saved_headers.items():
        if name in part:
            part.replace_header(name, value)
        else:
            part[name] = value


def rebuild_pdf(path: Path, transformer: Transformer, old_header: date, new_header: date) -> tuple[int, int]:
    with pdfplumber.open(path) as pdf:
        old_lines = []
        for page in pdf.pages:
            old_lines.extend((page.extract_text() or "").splitlines())
    new_lines = [transformer.transform(line, old_header, new_header) for line in old_lines]
    if "ArialUnicodePoint" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ArialUnicodePoint", str(FONT_PATH)))
    temporary = path.with_suffix(".point-edit.pdf")
    canvas = Canvas(str(temporary), pagesize=A4)
    width, height = A4
    canvas.setFillColor(HexColor("#173b57")); canvas.rect(0, height - 92, width, 92, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#ffffff")); canvas.setFont("ArialUnicodePoint", 16)
    canvas.drawString(48, height - 57, new_lines[0] if new_lines else "Документ")
    canvas.setFillColor(HexColor("#202124")); y = height - 130
    for index, line in enumerate(new_lines[1:], start=1):
        canvas.setFont("ArialUnicodePoint", 14 if index == 1 else 10)
        wrapped = []
        current = ""
        for word in line.split():
            if len(current) + len(word) + 1 > 90:
                wrapped.append(current); current = word
            else:
                current = f"{current} {word}".strip()
        wrapped.append(current)
        for item in wrapped:
            if y < 60:
                canvas.showPage(); y = height - 70
            canvas.drawString(48, y, item); y -= 18
        if not line:
            y -= 8
    canvas.save()
    temporary.replace(path)
    return len(old_lines), len(new_lines)


def rebuild_pdf_payload(
    payload: bytes,
    transformer: Transformer,
    old_header: date,
    new_header: date,
    *,
    generated: bool,
) -> bytes:
    with pdfplumber.open(BytesIO(payload)) as pdf:
        old_lines = []
        for page in pdf.pages:
            old_lines.extend((page.extract_text() or "").splitlines())
    new_lines = [
        transformer.transform(
            line, old_header, new_header, generated=generated
        )
        for line in old_lines
    ]
    compact_lines = [line.strip() for line in old_lines if line.strip()]
    if compact_lines and (
        sum(len(line) <= 2 for line in compact_lines) / len(compact_lines) > 0.35
        or (len(compact_lines) >= 8 and len(compact_lines[0]) <= 2)
    ):
        new_lines = [
            "Счёт за услуги",
            f"Получатель: {TARGET_FULL_NAME}",
            "Счёт № 704218",
            "Дата выставления: 29 июля 2026",
            "Срок оплаты: 5 августа 2026",
            "Сумма к оплате: 1 290 руб.",
            "Статус: ожидает оплаты",
            "Документ не содержит платёжных реквизитов или паспортных данных.",
        ]
    if "ArialUnicodePoint" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ArialUnicodePoint", str(FONT_PATH)))
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4)
    width, height = A4
    canvas.setFillColor(HexColor("#173b57")); canvas.rect(0, height - 92, width, 92, fill=1, stroke=0)
    canvas.setFillColor(HexColor("#ffffff")); canvas.setFont("ArialUnicodePoint", 16)
    canvas.drawString(48, height - 57, new_lines[0] if new_lines else "Документ")
    canvas.setFillColor(HexColor("#202124")); y = height - 130
    for index, line in enumerate(new_lines[1:], start=1):
        canvas.setFont("ArialUnicodePoint", 14 if index == 1 else 10)
        wrapped = []
        current = ""
        for word in line.split():
            if len(current) + len(word) + 1 > 90:
                wrapped.append(current); current = word
            else:
                current = f"{current} {word}".strip()
        wrapped.append(current)
        for item in wrapped:
            if y < 60:
                canvas.showPage(); y = height - 70
            canvas.drawString(48, y, item); y -= 18
        if not line:
            y -= 8
    canvas.save()
    return output.getvalue()


def edit_dataset(root: Path, report_path: Path) -> None:
    personal_emails = collect_personal_emails(root)
    product_names = collect_ozon_product_names(root)
    transformer = Transformer(personal_emails, product_names)
    schedules = new_header_dates(root)
    before_attachment_hashes = {}
    after_attachment_hashes = {}
    original_structures = {}
    pdf_name_map: dict[str, str] = {}
    pdf_stats = {}
    file_mapping: dict[str, str] = {}

    attachment_dir = root / "attachment_without_drive" / "attachments"
    for path in sorted(attachment_dir.glob("*.pdf")):
        old_name = path.name
        new_name = transformer.transform(old_name, date(2026, 7, 29), date(2026, 7, 29))
        new_name = unicodedata.normalize("NFC", new_name)
        new_name = re.sub(r"[^\w .()\-А-Яа-яЁё]", "_", new_name)
        pdf_name_map[unicodedata.normalize("NFC", old_name)] = new_name
        target = path.with_name(new_name)
        if target != path:
            path.rename(target); path = target
        old_lines, new_lines = rebuild_pdf(path, transformer, date(2026, 7, 29), date(2026, 7, 29))
        pdf_stats[path.name] = {"old_lines": old_lines, "new_lines": new_lines, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    for path, new_date in schedules.items():
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        generated = path.name.startswith("generated_")
        relative_path = str(path.relative_to(root))
        old_date = safe_old_date(message, new_date)
        original_structures[str(path.relative_to(root))] = [part.get_content_type() for part in message.walk()]
        before_attachment_hashes[str(path.relative_to(root))] = content_hashes(message)

        for header in list(message.keys()):
            if header.lower() in TRANSPORT_HEADERS or header.lower().startswith("x-"):
                del message[header]

        for header in ("To", "Cc", "Bcc"):
            while header in message:
                del message[header]
        message["To"] = f"{TARGET_DISPLAY_NAME} <{TARGET_EMAIL}>"

        for header in ("From", "Reply-To", "Sender", "List-Unsubscribe", "List-Help", "List-Post"):
            if header in message:
                message.replace_header(
                    header,
                    transformer.transform(
                        str(message[header]), old_date.date(), new_date.date()
                    ),
                )

        if "Subject" in message:
            message.replace_header("Subject", transformer.transform(str(message["Subject"]), old_date.date(), new_date.date(), generated=generated))
        if "Date" in message:
            message.replace_header("Date", format_datetime(new_date))
        else:
            message["Date"] = format_datetime(new_date)
        new_message_id = f"<{hashlib.sha256(str(path.relative_to(root)).encode()).hexdigest()[:24]}@mail.example>"
        if "Message-ID" in message:
            message.replace_header("Message-ID", new_message_id)
        else:
            message["Message-ID"] = new_message_id
        for header in ("References", "In-Reply-To"):
            if header in message:
                message.replace_header(header, re.sub(r"<[^>]+>", new_message_id, str(message[header])))

        # Point-edit custom outer headers too (for example X-Mailer, X-email and
        # Require-Recipient-Valid-Since), while keeping MIME boundary headers
        # byte-safe and structurally valid.
        protected_outer_headers = {
            "mime-version", "date", "message-id", "to", "subject", "from",
            "reply-to", "sender", "references", "in-reply-to",
        }
        for header in list(message.keys()):
            lower_header = header.lower()
            if lower_header.startswith("content-") or lower_header in protected_outer_headers:
                continue
            message.replace_header(
                header,
                transformer.transform(
                    str(message[header]), old_date.date(), new_date.date(), generated=generated
                ),
            )

        for part_index, part in enumerate(list(message.walk())):
            if part is not message:
                for header in list(part.keys()):
                    if header.lower() in TRANSPORT_HEADERS or header.lower().startswith("x-"):
                        del part[header]
                for header in ("Cc", "Bcc"):
                    while header in part:
                        del part[header]
                if "To" in part:
                    part.replace_header("To", f"{TARGET_DISPLAY_NAME} <{TARGET_EMAIL}>")
                if "Date" in part:
                    part.replace_header("Date", format_datetime(new_date))
                if "Message-ID" in part:
                    nested_id = f"<{hashlib.sha256((relative_path + str(part_index)).encode()).hexdigest()[:24]}@mail.example>"
                    part.replace_header("Message-ID", nested_id)
                for header in list(part.keys()):
                    if header.lower().startswith("content-") or header.lower() in {"mime-version", "date", "message-id", "to"}:
                        continue
                    part.replace_header(
                        header,
                        transformer.transform(
                            str(part[header]), old_date.date(), new_date.date(), generated=generated
                        ),
                    )
            if part.is_multipart():
                continue
            if part.get_content_maintype() == "text":
                try:
                    current = part.get_content()
                except Exception:
                    continue
                transformed = transformer.transform(current, old_date.date(), new_date.date(), generated=generated)
                transformed = enforce_scenario_dates(relative_path, transformed)
                update_text_part(part, transformed)
            elif part.get_content_type() == "application/pdf":
                filename = part.get_filename() or "document.pdf"
                normalized_filename = unicodedata.normalize("NFC", filename)
                new_filename = pdf_name_map.get(
                    normalized_filename,
                    unicodedata.normalize("NFC", transformer.transform(filename, old_date.date(), new_date.date())),
                )
                external = next(
                    (
                        candidate for candidate in attachment_dir.glob("*.pdf")
                        if unicodedata.normalize("NFC", candidate.name) == unicodedata.normalize("NFC", new_filename)
                    ),
                    attachment_dir / new_filename,
                )
                if external.exists():
                    payload = external.read_bytes()
                else:
                    payload = rebuild_pdf_payload(
                        part.get_payload(decode=True) or b"",
                        transformer,
                        old_date.date(),
                        new_date.date(),
                        generated=generated,
                    )
                part.set_payload(payload)
                while "Content-Transfer-Encoding" in part:
                    del part["Content-Transfer-Encoding"]
                encoders.encode_base64(part)
                if "Content-Disposition" in part:
                    part.set_param("filename", new_filename, header="Content-Disposition", replace=True)
                else:
                    part.add_header("Content-Disposition", "attachment", filename=new_filename)

        path.write_bytes(message.as_bytes(policy=policy.SMTP))
        reparsed = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        after_attachment_hashes[str(path.relative_to(root))] = content_hashes(reparsed)
        if [part.get_content_type() for part in reparsed.walk()] != original_structures[str(path.relative_to(root))]:
            raise RuntimeError(f"MIME structure changed: {path}")

        new_eml_name = transformer.transform(
            path.name, old_date.date(), new_date.date(), generated=generated
        )
        new_eml_name = unicodedata.normalize("NFC", new_eml_name)
        new_eml_name = re.sub(r"[^\w .()\-А-Яа-яЁё]", "_", new_eml_name)
        final_path = path
        if new_eml_name != path.name:
            destination = path.with_name(new_eml_name)
            if destination.exists():
                destination = path.with_name(f"{path.stem}-{hashlib.sha256(path.name.encode()).hexdigest()[:6]}.eml")
            path.rename(destination)
            final_path = destination
            transformer.counts["filenames"] += 1
        file_mapping[relative_path] = str(final_path.relative_to(root))

    manifest = root / "attachment_without_drive" / "manifest.csv"
    if manifest.exists():
        rows = list(csv.reader(manifest.read_text(encoding="utf-8").splitlines()))
        for row in rows[1:]:
            if row:
                row[:] = [transformer.transform(cell, date(2026, 7, 29), date(2026, 7, 29)) for cell in row]
                if len(row) >= 5:
                    row[4] = pdf_name_map.get(unicodedata.normalize("NFC", row[4]), row[4])
        with manifest.open("w", newline="", encoding="utf-8") as stream:
            csv.writer(stream).writerows(rows)

    unchanged_non_pdf = 0
    changed_pdf = 0
    for rel, before in before_attachment_hashes.items():
        after = after_attachment_hashes[rel]
        if len(before) != len(after):
            raise RuntimeError(f"Attachment count changed: {rel}")
        for old, new in zip(before, after):
            if old[0] == "application/pdf":
                if old[2] != new[2]:
                    changed_pdf += 1
            elif old[2] != new[2]:
                raise RuntimeError(f"Non-PDF attachment changed: {rel}: {old[1]}")
            else:
                unchanged_non_pdf += 1

    report = {
        "root": str(root),
        "messages": len(schedules),
        "personal_recipient_address_count": len(personal_emails),
        "personal_recipient_address_hashes": sorted(
            hashlib.sha256(value.encode()).hexdigest()[:12]
            for value in personal_emails if value != TARGET_EMAIL
        ),
        "replacement_counts": dict(transformer.counts),
        "url_mapping_count": len(transformer.url_map),
        "identifier_mapping_count": len(transformer.id_map),
        "phone_mapping_count": len(transformer.phone_map),
        "address_mapping_count": len(transformer.address_map),
        "ozon_product_mapping_count": len(transformer.product_map),
        "non_pdf_attachments_sha256_unchanged": unchanged_non_pdf,
        "embedded_pdf_attachments_updated": changed_pdf,
        "pdfs": pdf_stats,
        "file_mapping": file_mapping,
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("root", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    edit_dataset(args.root.resolve(), args.report.resolve())


if __name__ == "__main__":
    main()
