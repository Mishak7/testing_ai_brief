#!/usr/bin/env python3
"""CLI for uploading prepared EML messages to Rambler via IMAP."""

from __future__ import annotations

import argparse
import getpass
import imaplib
import json
import os
import ssl
import sys
import uuid
from datetime import datetime, timezone
from email import policy
from email.parser import BytesParser
from email.utils import format_datetime
from pathlib import Path
from typing import Iterable, Sequence


HOST = "imap.rambler.ru"
PORT = 993
DESTINATION_FOLDER = "INBOX"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_MESSAGES_DIR = SCRIPT_DIR / "messages"
SUBSETS_DIRNAME = "subsets"
SENT_DIRNAME = "sent"
CREDENTIALS_FILENAME = ".rambler_credentials.json"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rambler-imap",
        description="Загрузка выбранных .eml-писем в Rambler по IMAP.",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--subset",
        action="append",
        metavar="NAME",
        help=(
            "загрузить все .eml из messages/subsets/NAME и вложенных папок; "
            "можно указать несколько раз"
        ),
    )
    selection.add_argument(
        "--file",
        metavar="PATH_OR_NAME",
        help="загрузить одно письмо по пути или уникальному имени файла",
    )
    selection.add_argument(
        "--all",
        action="store_true",
        help="загрузить все доступные .eml (явный режим)",
    )
    selection.add_argument(
        "--list-subsets",
        action="store_true",
        help="показать сабсеты и количество писем",
    )
    parser.add_argument(
        "--messages-dir",
        type=Path,
        default=DEFAULT_MESSAGES_DIR,
        help=f"корневая папка писем (по умолчанию: {DEFAULT_MESSAGES_DIR})",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="показать выбранные письма без подключения и загрузки",
    )
    parser.add_argument(
        "--login",
        metavar="EMAIL",
        help="логин Rambler; имеет приоритет над сохранённым логином",
    )
    parser.add_argument(
        "--remember-credentials",
        action="store_true",
        help="сохранить логин и пароль приложения локально для следующих запусков",
    )
    parser.add_argument(
        "--clear-credentials",
        action="store_true",
        help="удалить локально сохранённые логин и пароль",
    )
    return parser


def subsets_dir(messages_dir: Path) -> Path:
    return messages_dir / SUBSETS_DIRNAME


def discover_subsets(messages_dir: Path) -> list[Path]:
    root = subsets_dir(messages_dir)
    if not root.is_dir():
        return []
    return sorted(
        path for path in root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def eml_files_in(directory: Path, *, recursive: bool = False) -> list[Path]:
    iterator: Iterable[Path]
    iterator = directory.rglob("*.eml") if recursive else directory.glob("*.eml")
    return sorted(path.resolve() for path in iterator if path.is_file())


def available_eml_files(messages_dir: Path) -> list[Path]:
    """Return source EML files, never files already archived in sent/."""
    sent_dir = (messages_dir / SENT_DIRNAME).resolve()
    result = []
    for path in messages_dir.rglob("*.eml"):
        resolved = path.resolve()
        if resolved == sent_dir or sent_dir in resolved.parents:
            continue
        result.append(resolved)
    return sorted(result)


def resolve_named_file(value: str, messages_dir: Path) -> Path:
    supplied = Path(value).expanduser()
    direct_candidates = [supplied]
    if not supplied.is_absolute():
        direct_candidates.append(messages_dir / supplied)

    for candidate in direct_candidates:
        if candidate.is_file():
            resolved = candidate.resolve()
            if resolved.suffix.lower() != ".eml":
                raise ValueError(f"Файл должен иметь расширение .eml: {resolved}")
            return resolved

    if supplied.name != value:
        raise ValueError(f"Файл не найден: {value}")

    matches = [
        path for path in available_eml_files(messages_dir)
        if path.name == value
    ]
    if not matches:
        raise ValueError(f"Письмо с именем {value!r} не найдено")
    if len(matches) > 1:
        rendered = "\n  ".join(str(path) for path in matches)
        raise ValueError(
            f"Имя {value!r} неоднозначно. Укажите путь:\n  {rendered}"
        )
    return matches[0]


def resolve_selection(args: argparse.Namespace, parser: argparse.ArgumentParser) -> list[Path]:
    messages_dir = args.messages_dir.expanduser().resolve()

    if args.subset:
        known = {path.name: path for path in discover_subsets(messages_dir)}
        selected: list[Path] = []
        unknown = [name for name in args.subset if name not in known]
        if unknown:
            choices = ", ".join(known) or "сабсеты не найдены"
            rendered_unknown = ", ".join(repr(name) for name in unknown)
            parser.error(f"неизвестный сабсет {rendered_unknown}; доступны: {choices}")
        for subset_name in args.subset:
            selected.extend(eml_files_in(known[subset_name], recursive=True))
        return selected

    if args.file:
        try:
            return [resolve_named_file(args.file, messages_dir)]
        except ValueError as error:
            parser.error(str(error))

    if args.all:
        return available_eml_files(messages_dir)

    parser.error("укажите --subset, --file, --all или --list-subsets")


def print_subsets(messages_dir: Path) -> None:
    directories = discover_subsets(messages_dir)
    if not directories:
        print(f"Сабсеты не найдены: {subsets_dir(messages_dir)}")
        return

    print("Доступные сабсеты:")
    for directory in directories:
        count = len(eml_files_in(directory, recursive=True))
        print(f"  {directory.name:<32} {count} .eml")


def attachment_count(eml_path: Path) -> int:
    message = BytesParser(policy=policy.default).parsebytes(eml_path.read_bytes())
    return sum(1 for _ in message.iter_attachments())


def unique_destination(sent_dir: Path, source: Path) -> Path:
    destination = sent_dir / source.name
    if not destination.exists():
        return destination
    return sent_dir / f"{source.stem}-{uuid.uuid4().hex[:8]}{source.suffix}"


def write_log(
    log_file: Path,
    filename: str,
    tracking_id: str,
    message_id: str,
    status: str,
    details: str = "",
) -> None:
    timestamp = datetime.now(timezone.utc).isoformat()
    clean_details = details.replace("\t", " ").replace("\n", " ")
    with log_file.open("a", encoding="utf-8") as log:
        log.write(
            f"{timestamp}\t{filename}\t{tracking_id}\t"
            f"{message_id}\t{status}\t{clean_details}\n"
        )


def credentials_file() -> Path:
    return SCRIPT_DIR / CREDENTIALS_FILENAME


def load_saved_credentials(path: Path | None = None) -> tuple[str, str] | None:
    path = path or credentials_file()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    login = str(data.get("login") or "").strip()
    password = str(data.get("password") or "")
    if not login or not password:
        return None
    return login, password


def save_credentials(login: str, password: str, path: Path | None = None) -> None:
    path = path or credentials_file()
    payload = json.dumps(
        {"login": login, "password": password},
        ensure_ascii=False,
        indent=2,
    )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(payload)
        file.write("\n")
    os.chmod(path, 0o600)


def clear_credentials(path: Path | None = None) -> bool:
    path = path or credentials_file()
    if not path.exists():
        return False
    path.unlink()
    return True


def resolve_credentials(
    *,
    login_override: str | None = None,
) -> tuple[str, str]:
    saved = load_saved_credentials()
    saved_login, saved_password = saved or ("", "")
    login = (
        os.environ.get("RAMBLER_LOGIN")
        or login_override
        or saved_login
        or input("Логин Rambler (полный email): ").strip()
    )
    matching_saved_password = saved_password if saved_login == login else ""
    password = (
        os.environ.get("RAMBLER_PASSWORD")
        or matching_saved_password
        or getpass.getpass("Пароль приложения Rambler: ")
    )
    return login, password


def prepare_message(eml_path: Path, login: str, now: datetime):
    raw_template = eml_path.read_bytes()
    raw_template = raw_template.replace(b"{{TO}}", login.encode("utf-8"))
    raw_template = raw_template.replace(
        b"{{DATE}}", format_datetime(now).encode("ascii")
    )
    message = BytesParser(policy=policy.SMTP).parsebytes(raw_template)

    if "To" not in message:
        message["To"] = login
    if "Date" not in message:
        message["Date"] = format_datetime(now)
    if "Message-ID" not in message:
        message["Message-ID"] = f"<{uuid.uuid4().hex}@mail.example>"
    while "X-Synthetic-ID" in message:
        del message["X-Synthetic-ID"]
    return message


def upload(
    files: Sequence[Path],
    messages_dir: Path,
    *,
    login_override: str | None = None,
    remember_credentials: bool = False,
) -> int:
    login, password = resolve_credentials(login_override=login_override)

    sent_dir = messages_dir / SENT_DIRNAME
    log_file = SCRIPT_DIR / "upload.log"
    sent_dir.mkdir(parents=True, exist_ok=True)

    connection = None
    uploaded = 0
    failed = 0
    try:
        print(f"Подключение к {HOST}:{PORT}...")
        connection = imaplib.IMAP4_SSL(
            host=HOST,
            port=PORT,
            ssl_context=ssl.create_default_context(),
            timeout=60,
        )
        connection.login(login, password)
        if remember_credentials:
            save_credentials(login, password)
            print(f"Логин и пароль сохранены локально: {credentials_file()}")
        print(f"Найдено писем: {len(files)}")

        for number, eml_path in enumerate(files, start=1):
            tracking_id = uuid.uuid4().hex[:12]
            now = datetime.now(timezone.utc)
            message_id = ""
            try:
                message = prepare_message(eml_path, login, now)
                message_id = str(message["Message-ID"])
                raw_message = message.as_bytes(policy=policy.SMTP)
                status, response = connection.append(
                    DESTINATION_FOLDER,
                    None,
                    imaplib.Time2Internaldate(now),
                    raw_message,
                )
                if status != "OK":
                    raise RuntimeError(f"APPEND вернул {status}: {response!r}")

                destination = unique_destination(sent_dir, eml_path)
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(raw_message)
                uploaded += 1
                try:
                    source_display = str(eml_path.relative_to(messages_dir))
                except ValueError:
                    source_display = str(eml_path)
                write_log(
                    log_file,
                    eml_path.name,
                    tracking_id,
                    message_id,
                    "OK",
                    f"source={source_display}; sent_copy={destination.name}",
                )
                print(f"[{number}/{len(files)}] Загружено: {eml_path.name}")
            except Exception as error:
                failed += 1
                write_log(
                    log_file,
                    eml_path.name,
                    tracking_id,
                    message_id,
                    "ERROR",
                    str(error),
                )
                print(
                    f"[{number}/{len(files)}] Ошибка: {eml_path.name}: {error}",
                    file=sys.stderr,
                )
    finally:
        if connection is not None:
            try:
                connection.logout()
            except Exception:
                pass

    print(f"\nУспешно загружено: {uploaded}")
    print(f"Ошибок: {failed}")
    print(f"Копии отправленных писем: {sent_dir}")
    print(f"Журнал внутренних ID: {log_file}")
    return 1 if failed else 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    messages_dir = args.messages_dir.expanduser().resolve()

    if args.clear_credentials:
        removed = clear_credentials()
        status = "удалены" if removed else "не были сохранены"
        print(f"Локальные учётные данные {status}: {credentials_file()}")
        if not (args.list_subsets or args.subset or args.file or args.all):
            return 0

    if args.list_subsets:
        print_subsets(messages_dir)
        return 0

    files = resolve_selection(args, parser)
    if not files:
        target = f"сабсете {', '.join(args.subset)!r}" if args.subset else "выборке"
        print(f"В {target} нет .eml-файлов")
        return 0

    if args.dry_run:
        print(f"Выбрано писем: {len(files)}")
        for path in files:
            try:
                display = path.relative_to(messages_dir)
            except ValueError:
                display = path
            count = attachment_count(path)
            suffix = f" ({count} вложение)" if count == 1 else (
                f" ({count} вложения)" if count else ""
            )
            print(f"  {display}{suffix}")
        return 0

    return upload(
        files,
        messages_dir,
        login_override=args.login,
        remember_credentials=args.remember_credentials,
    )


if __name__ == "__main__":
    raise SystemExit(main())
