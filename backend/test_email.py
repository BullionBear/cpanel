"""Test script to send a test email."""
import asyncio
import sys
from pathlib import Path

# Add app to path
sys.path.insert(0, str(Path(__file__).parent))

from app.services.email_service import email_service
from app.core.config import settings


async def main():
    """Send test email."""
    # Check if email credentials are configured
    if not settings.SMTP_USERNAME or not settings.SMTP_PASSWORD:
        print("ERROR: SMTP_USERNAME and SMTP_PASSWORD must be set in .env file")
        print(f"Current SMTP_URL: {settings.SMTP_URL}")
        print(f"Current SMTP_USERNAME: {settings.SMTP_USERNAME or '(not set)'}")
        print(f"Current SMTP_PASSWORD: {'*' * len(settings.SMTP_PASSWORD) if settings.SMTP_PASSWORD else '(not set)'}")
        sys.exit(1)

    print(f"Sending test email to: coastq22889@icloud.com")
    print(f"From: {settings.SMTP_FROM_EMAIL or settings.SMTP_USERNAME}")
    print(f"SMTP Server: {settings.SMTP_URL}")
    print()

    success = await email_service.send_test_email("coastq22889@icloud.com")

    if success:
        print("✅ Test email sent successfully!")
    else:
        print("❌ Failed to send test email. Check logs for details.")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())

