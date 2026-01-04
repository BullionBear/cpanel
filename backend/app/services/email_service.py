"""Email service for sending emails via SMTP."""
import aiosmtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from typing import Optional
import logging

from app.core.config import settings

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending emails via SMTP."""

    def __init__(self):
        """Initialize email service with SMTP settings."""
        self.smtp_host, self.smtp_port = self._parse_smtp_url(settings.SMTP_URL)
        self.smtp_username = settings.SMTP_USERNAME
        self.smtp_password = settings.SMTP_PASSWORD
        self.from_email = settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME
        self.from_name = settings.SMTP_FROM_NAME

    @staticmethod
    def _parse_smtp_url(smtp_url: str) -> tuple[str, int]:
        """Parse SMTP URL into host and port."""
        if ":" in smtp_url:
            host, port = smtp_url.rsplit(":", 1)
            return host, int(port)
        return smtp_url, 587  # Default STARTTLS port

    async def send_email(
        self,
        to_email: str,
        subject: str,
        body: str,
        html_body: Optional[str] = None,
    ) -> bool:
        """
        Send an email.

        Args:
            to_email: Recipient email address
            subject: Email subject
            body: Plain text email body
            html_body: Optional HTML email body

        Returns:
            True if email was sent successfully, False otherwise
        """
        try:
            # Create message
            if html_body:
                message = MIMEMultipart("alternative")
                message["Subject"] = subject
                message["From"] = f"{self.from_name} <{self.from_email}>"
                message["To"] = to_email

                # Add plain text and HTML parts
                text_part = MIMEText(body, "plain")
                html_part = MIMEText(html_body, "html")
                message.attach(text_part)
                message.attach(html_part)
            else:
                message = MIMEText(body, "plain")
                message["Subject"] = subject
                message["From"] = f"{self.from_name} <{self.from_email}>"
                message["To"] = to_email

            # Send email using SMTP
            # Use STARTTLS with port 587 (more reliable than port 465 with SSL)
            if self.smtp_port == 587:
                # STARTTLS connection (port 587 uses STARTTLS)
                await aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    username=self.smtp_username,
                    password=self.smtp_password,
                    use_tls=False,
                    start_tls=True,
                    timeout=30,
                )
            elif self.smtp_port == 465:
                # SSL connection (port 465 uses SSL/TLS from the start)
                # Create SSL context
                ssl_context = ssl.create_default_context()
                
                # Use SMTP with SSL for port 465
                async with aiosmtplib.SMTP(
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    use_tls=True,
                    tls_context=ssl_context,
                    timeout=30,
                ) as smtp:
                    await smtp.login(self.smtp_username, self.smtp_password)
                    await smtp.send_message(message)
            else:
                # Default: try STARTTLS
                await aiosmtplib.send(
                    message,
                    hostname=self.smtp_host,
                    port=self.smtp_port,
                    username=self.smtp_username,
                    password=self.smtp_password,
                    use_tls=False,
                    start_tls=True,
                    timeout=30,
                )

            logger.info(f"Email sent successfully to {to_email}")
            return True

        except Exception as e:
            logger.error(f"Failed to send email to {to_email}: {str(e)}")
            return False

    async def send_test_email(self, to_email: str) -> bool:
        """Send a test email."""
        subject = "Test Email from CPanel"
        body = """
This is a test email from the CPanel application.

If you received this email, the email service is working correctly.

Best regards,
CPanel Team
        """.strip()

        html_body = """
        <html>
          <body>
            <h2>Test Email from CPanel</h2>
            <p>This is a test email from the CPanel application.</p>
            <p>If you received this email, the email service is working correctly.</p>
            <hr>
            <p><em>Best regards,<br>CPanel Team</em></p>
          </body>
        </html>
        """

        return await self.send_email(to_email, subject, body, html_body)


# Create a singleton instance
email_service = EmailService()

