import os
import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from dotenv import load_dotenv

# Load .env file explicitly
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(REPO_ROOT, '.env'))

def send_developer_email():
    smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
    smtp_port = int(os.getenv('SMTP_PORT', '587'))
    smtp_user = os.getenv('SMTP_USER')
    smtp_pass = os.getenv('SMTP_PASSWORD')
    from_email = os.getenv('SMTP_FROM_EMAIL')
    from_name = os.getenv('SMTP_FROM_NAME', 'Mealie Planner')

    if not smtp_user or not smtp_pass:
        print("SMTP settings are missing. Cannot send email.")
        return False

    to_email = "paul@recipe-api.com"
    cc_emails = ["nathancrosty@gmail.com"]
    recipients = [to_email] + cc_emails

    subject = "Feedback on Recipe-API Search & Fuzzy Matching Support"

    body_html = """
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <p>Hi Paul,</p>

        <p>I hope you're doing well.</p>

        <p>I'm using <b>recipe-api.com</b> in my custom meal planning companion application to automatically pull per-serving macro and micro nutritional facts for recipes. I wanted to share some feedback regarding the API search endpoint behavior, particularly around fuzzy matching and query flexibility.</p>

        <p>Currently, the search endpoint is very strict, which makes it challenging to match recipe titles that are slightly descriptive or contain prep/modifier keywords directly. I ran some test lookups from my application, and here are the results:</p>

        <table border="1" cellpadding="8" style="border-collapse: collapse; border: 1px solid #ddd; font-size: 14px; margin-bottom: 20px;">
            <tr style="background-color: #f7f7f7; text-align: left;">
                <th>Search Query</th>
                <th>Result</th>
                <th>Notes</th>
            </tr>
            <tr>
                <td><code>Turkey Smash Burger</code></td>
                <td>❌ Not Found</td>
                <td>Searching for <code>Turkey Burger</code> succeeds and returns ID <code>e496776d-744c-415f-a10a-a825cc4724df</code>.</td>
            </tr>
            <tr>
                <td><code>Blackened Fish Tacos with Cilantro Pesto Slaw</code></td>
                <td>❌ Not Found</td>
                <td>Even searching for <code>Fish Tacos</code> fails, though the generic term <code>Tacos</code> succeeds and returns ID <code>50952b16-97df-4195-bb99-abb891ff337a</code>.</td>
            </tr>
            <tr>
                <td><code>Crispy Black Bean Tacos with Cilantro Lime Sauce</code></td>
                <td>❌ Not Found</td>
                <td>Searching for <code>Black Bean Tacos</code> succeeds and returns ID <code>8d20d36e-9eaa-4a17-8676-ce6c0a3f47e4</code>.</td>
            </tr>
        </table>

        <p>As a workaround on our side, we've had to implement an LLM-based query pre-processor to strip adjectives, modifiers (like "griddle", "crispy", "blackened"), and preparation descriptions ("with cilantro lime sauce") before hitting your API. However, doing fuzzy matching, keyword-based fallback searches, or adjective-stripping on the API backend itself would be a huge improvement for all developers integrating with your platform.</p>

        <p>Do you have any plans to introduce fuzzy matching or more flexible keyword queries to the search endpoint in the near future?</p>

        <p>Thanks for building a great API!</p>

        <p>Best regards,<br>
        Nathan Crosty</p>
    </body>
    </html>
    """

    msg = MIMEMultipart('alternative')
    msg['Subject'] = subject
    msg['From'] = f"{from_name} <{from_email}>"
    msg['To'] = to_email
    msg['Cc'] = ", ".join(cc_emails)

    msg.attach(MIMEText(body_html, 'html'))

    try:
        with smtplib.SMTP(smtp_host, smtp_port) as server:
            server.starttls()
            server.login(smtp_user, smtp_pass)
            server.sendmail(from_email, recipients, msg.as_string())
        print(f"Successfully sent developer email to {to_email} (CC: {cc_emails})")
        return True
    except Exception as e:
        print(f"Failed to send developer email: {e}")
        return False

if __name__ == "__main__":
    send_developer_email()
