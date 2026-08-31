#!/usr/bin/env python3
"""Rebase synthetic Brief Day mail dates to a chosen test date."""

from __future__ import annotations

import argparse
import re
from io import BytesIO
from datetime import date, datetime, timedelta, timezone
from email import policy
from email.parser import BytesParser
from email.utils import format_datetime, parsedate_to_datetime
from pathlib import Path

MSK = timezone(timedelta(hours=3))
MONTHS = {"января": 1, "февраля": 2, "марта": 3, "апреля": 4, "мая": 5,
          "июня": 6, "июля": 7, "августа": 8, "сентября": 9, "октября": 10,
          "ноября": 11, "декабря": 12}
MONTH_NAMES = {value: key for key, value in MONTHS.items()}
FONT = Path("/System/Library/Fonts/Supplemental/Arial Unicode.ttf")


def shift_text(text: str, delta: int) -> str:
    def numeric(match: re.Match[str]) -> str:
        try:
            old = date(int(match.group(3)), int(match.group(2)), int(match.group(1)))
            new = old + timedelta(days=delta)
            trailing_dot = match.group(4) or ""
            return f"{new.day:02d}.{new.month:02d}.{new.year:04d}{trailing_dot}"
        except ValueError:
            return match.group(0)

    def iso(match: re.Match[str]) -> str:
        try:
            old = date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
            new = old + timedelta(days=delta)
            return new.isoformat()
        except ValueError:
            return match.group(0)

    def ru(match: re.Match[str]) -> str:
        month = MONTHS.get(match.group(2).lower())
        if not month:
            return match.group(0)
        old = date(int(match.group(3)), month, int(match.group(1)))
        new = old + timedelta(days=delta)
        return f"{new.day} {MONTH_NAMES[new.month]} {new.year}"

    # Repair files produced by the old formatter, which used the optional
    # trailing-dot group as an in-date separator and emitted literal `None`.
    text = re.sub(r"(?<=\d)None(?=\d)", ".", text)
    text = re.sub(r"\b(\d{1,2})\.(\d{1,2})\.(20\d{2})(\.)?", numeric, text)
    text = re.sub(r"\b(20\d{2})-(\d{1,2})-(\d{1,2})\b", iso, text)
    return re.sub(r"\b(\d{1,2})\s+(%s)\s+(20\d{2})\b" % "|".join(MONTHS), ru, text, flags=re.I)


def old_datetime(message, fallback: datetime) -> datetime:
    try:
        parsed = parsedate_to_datetime(str(message.get("Date", "")))
        return (parsed.replace(tzinfo=MSK) if parsed.tzinfo is None else parsed.astimezone(MSK))
    except Exception:
        return fallback


def rebuilt_pdf_bytes(payload: bytes, delta: int, *, force: bool = False) -> bytes | None:
    import pdfplumber
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen.canvas import Canvas

    with pdfplumber.open(BytesIO(payload)) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    revised = shift_text(text, delta)
    if revised == text and not force:
        return None
    if "ArialUnicode" not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont("ArialUnicode", str(FONT)))
    output = BytesIO()
    canvas = Canvas(output, pagesize=A4)
    width, height = A4
    x, y = 46, height - 48
    canvas.setFont("ArialUnicode", 10)
    for paragraph in revised.splitlines() or [""]:
        words, line = paragraph.split(), ""
        for word in words or [""]:
            candidate = f"{line} {word}".strip()
            if len(candidate) > 74 or canvas.stringWidth(candidate, "ArialUnicode", 10) > width - 112:
                canvas.drawString(x, y, line); y -= 15; line = word
                if y < 46:
                    canvas.showPage(); canvas.setFont("ArialUnicode", 10); y = height - 48
            else:
                line = candidate
        canvas.drawString(x, y, line); y -= 15
        if y < 46:
            canvas.showPage(); canvas.setFont("ArialUnicode", 10); y = height - 48
    canvas.save()
    return output.getvalue()


def rebuild_pdf(path: Path, delta: int) -> bool:
    revised = rebuilt_pdf_bytes(path.read_bytes(), delta)
    if revised is None:
        return False
    path.write_bytes(revised)
    return True


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path("messages/subsets"))
    parser.add_argument("--date", default="2026-08-26")
    parser.add_argument(
        "--subset",
        action="append",
        help="rebase only the named first-level subset; may be repeated",
    )
    parser.add_argument("--skip-pdfs", action="store_true")
    parser.add_argument("--reformat-pdfs", action="store_true")
    parser.add_argument("--sync-only", action="store_true")
    args = parser.parse_args()
    anchor = datetime.fromisoformat(args.date).replace(tzinfo=MSK, hour=8, minute=0)
    available_subsets = {subset.name: subset for subset in args.root.iterdir() if subset.is_dir()}
    if args.subset:
        unknown = [name for name in args.subset if name not in available_subsets]
        if unknown:
            parser.error(
                "unknown subset(s): " + ", ".join(unknown)
                + "; available: " + ", ".join(sorted(available_subsets))
            )
        selected_subsets = [available_subsets[name] for name in args.subset]
    else:
        selected_subsets = list(available_subsets.values())
    messages_by_subset = {subset: sorted(subset.rglob("*.eml")) for subset in selected_subsets}
    updated = 0
    external = {path.name: path.read_bytes() for path in (args.root / "attachment_without_drive" / "attachments").glob("*.pdf")}
    for paths in messages_by_subset.values():
      for index, path in enumerate(paths):
        if args.reformat_pdfs:
            continue
        message = BytesParser(policy=policy.default).parsebytes(path.read_bytes())
        if args.sync_only:
            changed = False
            for part in message.walk():
                if part.get_content_type() != "application/pdf":
                    continue
                payload = external.get(part.get_filename() or "")
                if payload is None:
                    continue
                part.set_payload(payload)
                if "Content-Transfer-Encoding" in part:
                    del part["Content-Transfer-Encoding"]
                from email import encoders
                encoders.encode_base64(part)
                changed = True
            if changed:
                path.write_bytes(message.as_bytes(policy=policy.SMTP))
            continue
        planned = anchor + timedelta(minutes=index * 11)
        old = old_datetime(message, planned)
        delta = (planned.date() - old.date()).days
        if "Date" in message:
            message.replace_header("Date", format_datetime(planned))
        else:
            message["Date"] = format_datetime(planned)
        for part in message.walk():
            if part.is_multipart():
                continue
            if part.get_content_maintype() == "text":
                try:
                    current = part.get_content()
                except Exception:
                    continue
                revised = shift_text(current, delta)
                if revised != current:
                    subtype = part.get_content_subtype()
                    part.set_content(revised, subtype=subtype, charset="utf-8")
            elif part.get_content_type() == "application/pdf":
                if args.skip_pdfs:
                    continue
                filename = part.get_filename() or ""
                payload = external.get(filename)
                if payload is None:
                    payload = rebuilt_pdf_bytes(part.get_payload(decode=True) or b"", 28)
                if payload is not None:
                    part.set_payload(payload)
                    if "Content-Transfer-Encoding" in part:
                        del part["Content-Transfer-Encoding"]
                    from email import encoders
                    encoders.encode_base64(part)
        path.write_bytes(message.as_bytes(policy=policy.SMTP))
        updated += 1
    pdfs = 0
    if not args.skip_pdfs:
        for path in sorted((args.root / "attachment_without_drive" / "attachments").glob("*.pdf")):
            payload = rebuilt_pdf_bytes(path.read_bytes(), 0, force=args.reformat_pdfs)
            if payload is not None:
                path.write_bytes(payload)
                pdfs += 1
    print(f"Rebased {updated} EML files to {args.date}; updated {pdfs} external PDFs.")


if __name__ == "__main__":
    main()
