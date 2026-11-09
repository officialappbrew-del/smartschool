import os
import unittest
from unittest.mock import patch

from app.main import send_email


class EmailTemplateTests(unittest.TestCase):
    def test_sends_professional_html_and_plain_text_alternatives(self) -> None:
        body = (
            "You have been invited to administer Greenwood High School.\n\n"
            "Accept your invitation within 48 hours:\n"
            "https://greenwood.example.test/invitation/accept?token=sample-token\n\n"
            "If you were not expecting this invitation, you can ignore this message."
        )
        with (
            patch.dict(
                os.environ,
                {
                    "EMAIL_DELIVERY_MODE": "sync",
                    "EMAIL_HOST": "smtp.example.test",
                    "DEFAULT_FROM_EMAIL": "noreply@example.test",
                    "EMAIL_PORT": "587",
                    "EMAIL_USE_TLS": "true",
                    "EMAIL_USE_SSL": "false",
                },
                clear=True,
            ),
            patch("app.main.smtplib.SMTP") as smtp_factory,
        ):
            send_email("principal@example.test", "Your Northstar invitation", body)

        message = smtp_factory.return_value.__enter__.return_value.send_message.call_args.args[0]
        self.assertEqual(message.get_content_type(), "multipart/alternative")
        self.assertEqual(message.get_body(preferencelist=("plain",)).get_content().strip(), body)

        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("Northstar", html_body)
        self.assertIn("School management platform", html_body)
        self.assertIn("Accept invitation", html_body)
        self.assertIn(
            'href="https://greenwood.example.test/invitation/accept?token=sample-token"',
            html_body,
        )
        self.assertIn("automated message", html_body)

    def test_escapes_untrusted_subject_and_body_in_html(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "EMAIL_DELIVERY_MODE": "sync",
                    "EMAIL_HOST": "smtp.example.test",
                    "DEFAULT_FROM_EMAIL": "noreply@example.test",
                    "EMAIL_USE_TLS": "true",
                    "EMAIL_USE_SSL": "false",
                },
                clear=True,
            ),
            patch("app.main.smtplib.SMTP") as smtp_factory,
        ):
            send_email(
                "parent@example.test",
                "<script>unsafe</script>",
                "<img src=x onerror=alert(1)>",
            )

        message = smtp_factory.return_value.__enter__.return_value.send_message.call_args.args[0]
        html_body = message.get_body(preferencelist=("html",)).get_content()
        self.assertIn("&lt;script&gt;unsafe&lt;/script&gt;", html_body)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", html_body)
        self.assertNotIn("<script>", html_body)
        self.assertNotIn("<img", html_body)


if __name__ == "__main__":
    unittest.main()
