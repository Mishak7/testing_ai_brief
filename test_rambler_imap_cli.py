import tempfile
import unittest
from datetime import datetime, timezone
from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from pathlib import Path
from unittest.mock import patch

from rambler_imap_cli import (
    available_eml_files,
    attachment_count,
    build_parser,
    discover_subsets,
    load_saved_credentials,
    prepare_message,
    resolve_credentials,
    resolve_named_file,
    resolve_selection,
    save_credentials,
    upload,
)


class SelectionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.messages = Path(self.temporary.name)
        (self.messages / "subsets" / "baseline_profile").mkdir(parents=True)
        (self.messages / "subsets" / "travel").mkdir(parents=True)
        (self.messages / "sent").mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def make_eml(self, relative_path: str) -> Path:
        path = self.messages / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("Subject: Уведомление\n\nСодержание\n", encoding="utf-8")
        return path.resolve()

    def test_discovers_subset_directories(self):
        names = [path.name for path in discover_subsets(self.messages)]
        self.assertEqual(names, ["baseline_profile", "travel"])

    def test_subset_selects_nested_eml_files(self):
        expected = self.make_eml("subsets/travel/messages/ticket.eml")
        self.make_eml("subsets/baseline_profile/school.eml")
        parser = build_parser()
        args = parser.parse_args(
            ["--messages-dir", str(self.messages), "--subset", "travel"]
        )
        self.assertEqual(resolve_selection(args, parser), [expected])

    def test_can_select_multiple_subsets_with_one_command(self):
        baseline = self.make_eml("subsets/baseline_profile/school.eml")
        travel = self.make_eml("subsets/travel/ticket.eml")
        parser = build_parser()
        args = parser.parse_args(
            [
                "--messages-dir",
                str(self.messages),
                "--subset",
                "travel",
                "--subset",
                "baseline_profile",
            ]
        )
        self.assertEqual(resolve_selection(args, parser), [travel, baseline])

    def test_prepare_message_preserves_pdf_attachment(self):
        path = self.messages / "subsets" / "travel" / "document.eml"
        message = EmailMessage()
        message["From"] = "docs@travel.example"
        message["To"] = "mikhail.kozyrev@rambler.ru"
        message["Subject"] = "Документ"
        message.set_content("Документ приложен.")
        message.add_attachment(
            b"%PDF-1.4\n",
            maintype="application",
            subtype="pdf",
            filename="document.pdf",
        )
        path.write_bytes(message.as_bytes())

        prepared = prepare_message(path, "mikhail.kozyrev@rambler.ru", datetime.now(timezone.utc))

        self.assertEqual(attachment_count(path), 1)
        attachments = list(prepared.iter_attachments())
        self.assertEqual(len(attachments), 1)
        self.assertEqual(attachments[0].get_filename(), "document.pdf")

    def test_upload_sends_attachment_in_imap_append_payload(self):
        path = self.messages / "subsets" / "travel" / "document.eml"
        message = EmailMessage()
        message["From"] = "docs@travel.example"
        message["To"] = "mikhail.kozyrev@rambler.ru"
        message["Subject"] = "Документ"
        message.set_content("Документ приложен.")
        message.add_attachment(
            b"%PDF-1.4\n",
            maintype="application",
            subtype="pdf",
            filename="document.pdf",
        )
        path.write_bytes(message.as_bytes())

        class FakeImap:
            appended = b""

            def __init__(self, **_kwargs):
                pass

            def login(self, _login, _password):
                return "OK", []

            def append(self, _folder, _flags, _date, payload):
                type(self).appended = payload
                return "OK", [b"saved"]

            def logout(self):
                return "BYE", []

        with (
            patch("rambler_imap_cli.imaplib.IMAP4_SSL", FakeImap),
            patch("rambler_imap_cli.SCRIPT_DIR", self.messages),
            patch.dict(
                "os.environ",
                {"RAMBLER_LOGIN": "mikhail.kozyrev@rambler.ru", "RAMBLER_PASSWORD": "secret"},
            ),
        ):
            result = upload([path], self.messages)

        appended = BytesParser(policy=policy.default).parsebytes(FakeImap.appended)
        self.assertEqual(result, 0)
        self.assertTrue(path.exists())
        self.assertEqual(len(list(appended.iter_attachments())), 1)

    def test_filename_resolves_across_source_subsets(self):
        expected = self.make_eml("subsets/travel/ticket.eml")
        self.assertEqual(resolve_named_file("ticket.eml", self.messages), expected)

    def test_filename_must_be_unambiguous(self):
        self.make_eml("subsets/travel/notice.eml")
        self.make_eml("subsets/baseline_profile/notice.eml")
        with self.assertRaisesRegex(ValueError, "неоднозначно"):
            resolve_named_file("notice.eml", self.messages)

    def test_all_excludes_sent_archive(self):
        source = self.make_eml("subsets/travel/ticket.eml")
        self.make_eml("sent/already-uploaded.eml")
        self.assertEqual(available_eml_files(self.messages), [source])

    def test_saved_credentials_roundtrip_uses_private_file_mode(self):
        path = self.messages / "credentials.json"

        save_credentials("mikhail.kozyrev@rambler.ru", "secret", path)

        self.assertEqual(
            load_saved_credentials(path),
            ("mikhail.kozyrev@rambler.ru", "secret"),
        )
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_upload_remembers_credentials_after_successful_login(self):
        path = self.messages / "subsets" / "travel" / "notice.eml"
        message = EmailMessage()
        message["From"] = "service@example.test"
        message["To"] = "mikhail.kozyrev@rambler.ru"
        message["Subject"] = "Уведомление"
        message.set_content("Содержание")
        path.write_bytes(message.as_bytes())

        class FakeImap:
            def __init__(self, **_kwargs):
                pass

            def login(self, _login, _password):
                return "OK", []

            def append(self, _folder, _flags, _date, _payload):
                return "OK", [b"saved"]

            def logout(self):
                return "BYE", []

        with (
            patch("rambler_imap_cli.imaplib.IMAP4_SSL", FakeImap),
            patch("rambler_imap_cli.SCRIPT_DIR", self.messages),
            patch.dict(
                "os.environ",
                {"RAMBLER_LOGIN": "mikhail.kozyrev@rambler.ru", "RAMBLER_PASSWORD": "secret"},
                clear=True,
            ),
        ):
            result = upload([path], self.messages, remember_credentials=True)

        self.assertEqual(result, 0)
        self.assertEqual(
            load_saved_credentials(self.messages / ".rambler_credentials.json"),
            ("mikhail.kozyrev@rambler.ru", "secret"),
        )

    def test_resolve_credentials_uses_saved_values_without_prompting(self):
        save_credentials("mikhail.kozyrev@rambler.ru", "secret", self.messages / ".rambler_credentials.json")

        with (
            patch("rambler_imap_cli.SCRIPT_DIR", self.messages),
            patch.dict("os.environ", {}, clear=True),
            patch("builtins.input", side_effect=AssertionError("unexpected login prompt")),
            patch("rambler_imap_cli.getpass.getpass", side_effect=AssertionError("unexpected password prompt")),
        ):
            credentials = resolve_credentials()

        self.assertEqual(credentials, ("mikhail.kozyrev@rambler.ru", "secret"))

    def test_login_override_does_not_reuse_password_for_another_saved_login(self):
        save_credentials("old.user@rambler.ru", "old-secret", self.messages / ".rambler_credentials.json")

        with (
            patch("rambler_imap_cli.SCRIPT_DIR", self.messages),
            patch.dict("os.environ", {}, clear=True),
            patch("rambler_imap_cli.getpass.getpass", return_value="new-secret"),
        ):
            credentials = resolve_credentials(login_override="mikhail.kozyrev@rambler.ru")

        self.assertEqual(credentials, ("mikhail.kozyrev@rambler.ru", "new-secret"))


if __name__ == "__main__":
    unittest.main()
