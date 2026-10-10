"""Planner mail must look like mail from a real client: Gmail spam-filed it without these."""
from email import message_from_string

from mealie_planner.email_notifier import build_message, html_to_text


def test_message_has_date_msgid_and_text_part():
    msg = build_message("🍽️ Plan", "<h2>Hi</h2><p>Tacos &amp; slaw</p><ul><li>Mon</li></ul>",
                        "Mealie Planner", "service@cosmoslab.dev", ["a@example.com"])
    parsed = message_from_string(msg.as_string())
    assert parsed["Date"] and parsed["Message-ID"].endswith("@cosmoslab.dev>")
    types = [p.get_content_type() for p in parsed.walk() if not p.is_multipart()]
    assert types == ["text/plain", "text/html"]   # plain first: preferred-last is the HTML


def test_html_to_text_is_readable():
    t = html_to_text("<style>x{}</style><h2>Week</h2><p>Tacos &amp; slaw<br>Gyros</p><ul><li>Mon</li></ul>")
    assert t == "Week\nTacos & slaw\nGyros\n- Mon\n"
