import logging
import os
import smtplib
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from zoneinfo import ZoneInfo

from .config import (
    _DAILY_BRIEFING_GENERATION_SKILL_DEFINITION,
    _WEEKLY_THEMES_SYNOPSIS_SKILL_DEFINITION,
    APP_URL,
    FAMILY_NAMES,
    FAMILY_RECIPIENT_EMAILS,
    MEALIE_FRONTEND_URL,
    RDA,
    TIMEZONE,
)
from .utils import extract_ingredient_texts, get_active_week_strings

logger = logging.getLogger(__name__)

class EmailNotifier:
    def __init__(self, mealie_client, ai_client):
        # Lazy imports to avoid circular dependencies
        from .recipe_crawler import RecipeCrawler
        from .recipe_nutrition import RecipeNutrition
        self.client = mealie_client
        self.ai = ai_client
        self.crawler = RecipeCrawler(mealie_client, ai_client)
        self.nutrition = RecipeNutrition(mealie_client, ai_client)

    def _render_email_template(self, template_name: str, **context) -> str:
        """Render a Jinja2 email template from the project's templates/ directory."""
        from jinja2 import Environment, FileSystemLoader
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        templates_dir = os.path.join(base_dir, 'templates')
        env = Environment(loader=FileSystemLoader(templates_dir))
        if 'family_names' not in context:
            context['family_names'] = FAMILY_NAMES
        return env.get_template(template_name).render(**context)

    def send_email(self, subject, html_content):
        """Send an email using SMTP settings."""
        from mealie_planner.database import load_state_from_db
        state = load_state_from_db()
        emails_enabled = state.get('emails_enabled', True)
                
        if not emails_enabled:
            logger.warning(f"[Email] Emails are currently disabled. Skipping sending: '{subject}'")
            return False

        smtp_host = os.getenv('SMTP_HOST', 'smtp.gmail.com')
        smtp_port = int(os.getenv('SMTP_PORT', '587'))
        smtp_user = os.getenv('SMTP_USER')
        smtp_pass = os.getenv('SMTP_PASSWORD')
        from_email = os.getenv('SMTP_FROM_EMAIL')
        from_name = os.getenv('SMTP_FROM_NAME', 'Mealie Planner')

        # SMTP_USER/SMTP_PASSWORD are optional. The lab relay on 192.168.4.114:25
        # takes no credentials at all: it accepts this host by IP via postfix
        # `mynetworks` and does the authenticated hop to Purelymail itself, so the
        # provider password lives in exactly one place instead of in every app.
        # A host is still required.
        if not smtp_host:
            logger.warning("SMTP_HOST is not set. Cannot send email.")
            return False

        # Fetch recipients dynamically from all registered Mealie users
        recipients = []
        try:
            users = self.client.get_users()
            recipients = [u.get('email') for u in users if u.get('email')]
            if recipients:
                logger.info(f"[Email] Dynamically loaded recipients from Mealie: {recipients}")
        except Exception as e:
            logger.warning(f"[Email] Could not fetch Mealie users, falling back to static list: {e}")
            recipients = FAMILY_RECIPIENT_EMAILS

        # Apply per-recipient opt-outs saved by the admin UI
        disabled = state.get('disabled_recipient_emails', [])
        if disabled:
            recipients = [r for r in recipients if r not in disabled]
            logger.info(f"[Email] Filtered out disabled recipients {disabled}. Sending to: {recipients}")

        if not recipients:
            logger.info("No recipient emails found. Cannot send email.")
            return False

        msg = MIMEMultipart('alternative')
        msg['Subject'] = subject
        msg['From'] = f"{from_name} <{from_email}>"
        msg['To'] = ", ".join(recipients)

        msg.attach(MIMEText(html_content, 'html'))

        try:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                # Only negotiate TLS and authenticate when a credential is actually
                # configured, so pointing SMTP_HOST straight at an authenticating
                # provider keeps working exactly as before. Against the lab relay
                # both steps must be skipped: it presents the stock self-signed
                # snakeoil certificate, which starttls() rejects on modern Python,
                # and it has no SASL backend, so AUTH returns 503.
                if smtp_user and smtp_pass:
                    server.starttls()
                    server.login(smtp_user, smtp_pass)
                server.sendmail(from_email, recipients, msg.as_string())
            logger.info(f"Successfully sent email: '{subject}' to {recipients}")
            return True
        except Exception as e:
            logger.error(f"Failed to send email: {e}")
            return False

    def parse_recipe_details_for_ai(self, raw_details):
        """Extract and format ingredients and instructions for the AI prompt from raw details."""
        if not raw_details:
            return None
        ingredients = extract_ingredient_texts(raw_details)

        instructions = [i.get('text', '') for i in raw_details.get('recipeInstructions', []) if i.get('text')]
        
        return {
            "name": raw_details.get("name"),
            "description": raw_details.get("description"),
            "ingredients": ingredients,
            "instructions": instructions
        }

    def generate_daily_ai_summary(self, day_name, breakfast, lunch, dinner_title, recipe_details=None, prep_note=None, tomorrow_title=None, tomorrow_recipe_details=None, tomorrow_prep_note=None):
        """Call AI to generate a brief, practical daily kitchen briefing of the day's meals."""
        context = f"""Today ({day_name})'s Menu:
- Breakfast: {breakfast}
- Lunch: {lunch}
- Dinner: {dinner_title}

Today's Dinner details:
- Recipe description: {recipe_details.get('description', '') if recipe_details else ''}
- Ingredients: {', '.join(recipe_details.get('ingredients', [])) if recipe_details else ''}
- Instructions: {" ".join(recipe_details.get('instructions', []))[:1000] if recipe_details else ''}
- Manual Prep Note: {prep_note or ''}
"""
        if tomorrow_title:
            context += f"""
Tomorrow's Dinner details:
- Name: {tomorrow_title}
- Tomorrow's Instructions: {" ".join(tomorrow_recipe_details.get('instructions', []))[:1000] if tomorrow_recipe_details else ''}
- Tomorrow's Manual Prep Note: {tomorrow_prep_note or ''}
"""

        prompt = (
            "You are an expert in the 'Daily Briefing Generation Skill'.\n\n" +
            _DAILY_BRIEFING_GENERATION_SKILL_DEFINITION +
            "\n\n### CONTEXT FOR THIS INVOCATION:\n" +
            context +
            "\nReturn ONLY the single briefing paragraph as specified in the skill definition."
        )
        try:
            summary = self.ai.call(prompt, expect_json=False, temperature=0.4).strip()
            return summary
        except Exception as e:
            logger.error(f"[Email] Failed to generate AI summary: {e}")
            prep_str = f" Prep: {prep_note}" if prep_note else ""
            return f"Today we have {breakfast} for breakfast, {lunch} for lunch, and {dinner_title} for dinner.{prep_str}"

    def generate_weekly_themes_summary(self, meal_plans, start_date_str, end_date_str):
        """Call AI to generate a brief summary and thematic analysis of the upcoming week's meals."""
        dinners = []
        try:
            start_date = datetime.strptime(start_date_str, "%Y-%m-%d")
        except ValueError:
            return "A diverse week of planned dinners."
            
        for i in range(7):
            curr = start_date + timedelta(days=i)
            d_str = curr.strftime("%Y-%m-%d")
            day_name = curr.strftime("%A")
            dinner_item = next((p for p in meal_plans if p['date'][:10] == d_str and p['entryType'] == 'dinner'), None)
            title = "Eating Out"
            if dinner_item:
                title = dinner_item.get('title') or ""
                if dinner_item.get('recipeId') and not title:
                    try:
                        r = self.client.get_recipe_details(dinner_item['recipeId'])
                        title = r['name']
                    except Exception:
                        title = "Recipe Details Unavailable"
            dinners.append(f"- {day_name}: {title}")
            
        dinners_str = "\n".join(dinners)
        
        prompt = (
            "You are an expert in the 'Weekly Themes Synopsis Skill'.\n\n" +
            _WEEKLY_THEMES_SYNOPSIS_SKILL_DEFINITION +
            "\n\n### CONTEXT FOR THIS INVOCATION:\n" +
            f"Weekly Dinner Menu:\n{dinners_str}\n\n" +
            "Return ONLY the themes synopsis as specified in the skill definition."
        )
        try:
            return self.ai.call(prompt, expect_json=False, temperature=0.5).strip().strip('"').strip("'")
        except Exception as e:
            logger.error(f"[Email] Failed to generate weekly themes summary: {e}")
            return "A diverse week of planned dinners, highlighting fresh ingredients and easy-to-cook recipes."

    def build_daily_briefing_html(self, day_name, date_str, bf, ln, dn_title, dn_recipe, ai_prep_note, today_nutrients, ai_summary, weekly_content_html=None):
        """Build the daily briefing HTML email using a Jinja2 template."""
        is_blackstone = self.crawler.check_blackstone_compatibility(dn_recipe) if dn_recipe else False

        # Pre-process nutrients into display-ready dicts so the template stays logic-free
        nutrients = []
        for k, v in today_nutrients.items():
            unit = "kcal" if k == "calories" else ("mg" if k in ["sodium", "cholesterol"] else "g")
            target = RDA.get(k, 0.0)
            pct = round((v / target) * 100) if target > 0 else 0
            color = "#3C5A54"
            if k == "sodium" and pct > 100:
                color = "#EF5350"
            elif k == "fiber" and pct < 100:
                color = "#C66B3D"
            nutrients.append({"name": k, "value": v, "unit": unit, "pct": pct, "color": color})

        return self._render_email_template(
            'emails/daily_briefing.html',
            day_name=day_name,
            date_str=date_str,
            ai_summary=(ai_summary or "").strip().strip('"').strip("'"),
            breakfast=bf,
            lunch=ln,
            dinner_title=dn_title,
            dinner_recipe=dn_recipe,
            ai_prep_note=ai_prep_note,
            is_blackstone=is_blackstone,
            nutrients=nutrients,
            weekly_content_html=weekly_content_html or "",
            app_url=APP_URL,
            mealie_frontend_url=MEALIE_FRONTEND_URL,
        )

    def send_daily_reminder_email(self, date_str=None, weekly_content_html=None, subject_override=None):
        """Generate and send the daily meal briefing email.

        When `weekly_content_html` is supplied (e.g. the Saturday report), it is appended
        below the daily briefing so a single combined email is sent.
        """
        if not date_str:
            date_str = datetime.now(ZoneInfo(TIMEZONE)).strftime("%Y-%m-%d")
            
        try:
            dt = datetime.strptime(date_str, "%Y-%m-%d")
            day_name = dt.strftime("%A")
            tomorrow_dt = dt + timedelta(days=1)
            tomorrow_str = tomorrow_dt.strftime("%Y-%m-%d")
        except Exception as e:
            logger.error(f"[Email] Error parsing date: {e}")
            return False
            
        plans = self.client.get_meal_plan(date_str, tomorrow_str)
        if not plans:
            logger.info(f"[Email] No scheduled meals for {date_str}.")
            return False
            
        bf = next((p['title'] for p in plans if p['date'][:10] == date_str and p['entryType'] == 'breakfast'), "Staples")
        ln = next((p['title'] for p in plans if p['date'][:10] == date_str and p['entryType'] == 'lunch'), "Leftovers")
        
        dinner_item = next((p for p in plans if p['date'][:10] == date_str and p['entryType'] == 'dinner'), None)
        dn_title = "Eating Out"
        dn_recipe = None
        ai_prep_note = ""
        
        if dinner_item:
            ai_prep_note = dinner_item.get('text') or ""
            if dinner_item.get('recipeId'):
                try:
                    dn_recipe = self.client.get_recipe_details(dinner_item['recipeId'])
                    dn_title = dn_recipe['name']
                except Exception:
                    dn_title = "Recipe Details Unavailable"
            elif dinner_item.get('title'):
                dn_title = dinner_item['title']

        tomorrow_dinner_item = next((p for p in plans if p['date'][:10] == tomorrow_str and p['entryType'] == 'dinner'), None)
        tomorrow_title = "None scheduled"
        tomorrow_recipe = None
        tomorrow_prep_note = ""
        if tomorrow_dinner_item:
            tomorrow_prep_note = tomorrow_dinner_item.get('text') or ""
            if tomorrow_dinner_item.get('recipeId'):
                try:
                    tomorrow_recipe = self.client.get_recipe_details(tomorrow_dinner_item['recipeId'])
                    tomorrow_title = tomorrow_recipe['name']
                except Exception:
                    tomorrow_title = "Recipe Details Unavailable"
            elif tomorrow_dinner_item.get('title'):
                tomorrow_title = tomorrow_dinner_item['title']

        parsed_recipe_info = self.parse_recipe_details_for_ai(dn_recipe) if dn_recipe else None
        parsed_tomorrow_recipe_info = self.parse_recipe_details_for_ai(tomorrow_recipe) if tomorrow_recipe else None
        ai_summary = self.generate_daily_ai_summary(
            day_name=day_name, breakfast=bf, lunch=ln, dinner_title=dn_title,
            recipe_details=parsed_recipe_info, prep_note=ai_prep_note,
            tomorrow_title=tomorrow_title, tomorrow_recipe_details=parsed_tomorrow_recipe_info, tomorrow_prep_note=tomorrow_prep_note
        )
        
        daily_nutrition, _ = self.nutrition.calculate_nutrition_for_range(date_str, date_str)
        today_nutrients = daily_nutrition.get(date_str, {})
        
        html = self.build_daily_briefing_html(
            day_name=day_name, date_str=date_str, bf=bf, ln=ln, dn_title=dn_title,
            dn_recipe=dn_recipe, ai_prep_note=ai_prep_note, today_nutrients=today_nutrients,
            ai_summary=ai_summary, weekly_content_html=weekly_content_html
        )

        subject = subject_override or f"🍽️ Today's Meal Plan: {dn_title} ({day_name})"
        return self.send_email(subject, html)

    def send_saturday_report_email(self, start_date_str, end_date_str, exclude_text, freezer_items, low_staples_ids, special_requests=""):
        """Send summary of generated meal plan, prefixed with Saturday's daily briefing."""
        try:
            from .utils import get_active_week_strings
            active_start_str, active_end_str = get_active_week_strings()
            
            logger.info(f"[Email] Generating Saturday report for active week: {active_start_str} to {active_end_str} (triggered by plan generation from {start_date_str} to {end_date_str})...")
            meal_plans = self.client.get_meal_plan(active_start_str, active_end_str)
            daily_nutrients, averages = self.nutrition.calculate_nutrition_for_range(active_start_str, active_end_str)
            
            # Build structured meal rows for the template
            meal_rows = []
            start_date = datetime.strptime(active_start_str, "%Y-%m-%d")
            for i in range(7):
                curr = start_date + timedelta(days=i)
                d_str = curr.strftime("%Y-%m-%d")
                day_name_str = curr.strftime("%A")

                bf = next((p['title'] for p in meal_plans if p['date'][:10] == d_str and p['entryType'] == 'breakfast'), "Staples")
                ln = next((p['title'] for p in meal_plans if p['date'][:10] == d_str and p['entryType'] == 'lunch'), "Leftovers")

                dinner_item = next((p for p in meal_plans if p['date'][:10] == d_str and p['entryType'] == 'dinner'), None)
                dinner_name = "Eating Out"
                dinner_slug = None
                if dinner_item:
                    if dinner_item.get('recipeId'):
                        try:
                            r = self.client.get_recipe_details(dinner_item['recipeId'])
                            dinner_name = r['name']
                            dinner_slug = r.get('slug')
                        except Exception:
                            dinner_name = "Recipe Details Unavailable"
                    elif dinner_item.get('title'):
                        dinner_name = dinner_item['title']

                meal_rows.append({
                    "day_name": day_name_str,
                    "breakfast": bf,
                    "lunch": ln,
                    "dinner_name": dinner_name,
                    "dinner_slug": dinner_slug,
                })

            # Build structured nutrition rows for the template
            nut_rows = []
            for k, rda_val in RDA.items():
                avg_val = averages.get(k, 0.0)
                pct = round((avg_val / rda_val) * 100) if rda_val > 0 else 0
                unit = "kcal" if k == "calories" else ("mg" if k in ["sodium", "cholesterol"] else "g")
                nut_rows.append({"name": k, "avg": avg_val, "rda": rda_val, "pct": pct, "unit": unit})

            weekly_themes = self.generate_weekly_themes_summary(meal_plans, active_start_str, active_end_str)

            weekly_content_html = self._render_email_template(
                'emails/weekly_summary_block.html',
                weekly_themes=weekly_themes,
                meal_rows=meal_rows,
                nut_rows=nut_rows,
                mealie_frontend_url=MEALIE_FRONTEND_URL,
            )

            # Send a single combined email: today's daily briefing (first day of the newly
            # planned subset) followed by the full weekly plan summary built above.
            subject = f"🍽️ Your Weekly Meal Plan is Ready ({active_start_str} to {active_end_str})"
            return self.send_daily_reminder_email(
                start_date_str,
                weekly_content_html=weekly_content_html,
                subject_override=subject,
            )

        except Exception as e:
            logger.error(f"[Email] Failed Saturday report: {e}")
            return False

def send_email(subject, html_content):
    from .ai_client import AIClient
    from .unified_client import UnifiedMealieClient
    client = UnifiedMealieClient()
    ai = AIClient()
    notifier = EmailNotifier(client, ai)
    return notifier.send_email(subject, html_content)

def send_daily_reminder_email(date_str=None):
    from .ai_client import AIClient
    from .unified_client import UnifiedMealieClient
    client = UnifiedMealieClient()
    ai = AIClient()
    notifier = EmailNotifier(client, ai)
    return notifier.send_daily_reminder_email(date_str)

def send_saturday_qa_email():
    from .ai_client import AIClient
    from .unified_client import UnifiedMealieClient
    client = UnifiedMealieClient()
    ai = AIClient()
    notifier = EmailNotifier(client, ai)

    # Check if a plan already exists for the active week starting today (Saturday)
    try:
        start_str, end_str = get_active_week_strings()
        plans = client.get_meal_plan(start_str, end_str)
        dinners = [
            p for p in plans 
            if p['entryType'] == 'dinner' 
            and (p.get('recipeId') or p.get('title') or p.get('text'))
        ]
        if dinners:
            logger.warning(f"[Email] Plan already exists for Saturday ({start_str} to {end_str}). Skipping Saturday Q/A email.")
            return True
    except Exception as e:
        logger.error(f"[Email] Error checking plan existence for Saturday: {e}")

    subject = "📋 Weekly Meal Plan Questionnaire"
    body = f"""
    <h2>Hey there!</h2>
    <p>It's Saturday morning. Please take a moment to fill out the weekly meal plan questionnaire:</p>
    <p><a href="{APP_URL}" style="display: inline-block; padding: 10px 20px; background-color: #E58325; color: white; text-decoration: none; border-radius: 5px;">Fill out Questionnaire</a></p>
    <p>Thank you!</p>
    """
    return notifier.send_email(subject, body)

def setup_scheduler(mealie_client, ai_client):
    """Initialize and start the background scheduler for email notifications."""
    from apscheduler.schedulers.background import BackgroundScheduler
    from apscheduler.triggers.cron import CronTrigger
    
    scheduler = BackgroundScheduler(timezone=TIMEZONE)

    # We need closures to capture the notifier instance
    def daily_reminder_job():
        send_daily_reminder_email()
    
    def saturday_qa_job():
        send_saturday_qa_email()

    # 1. Saturday Q/A email at 8:00 AM (only sends if no plan exists)
    scheduler.add_job(
        saturday_qa_job,
        CronTrigger(day_of_week='sat', hour=8, minute=0, timezone=TIMEZONE),
        id='saturday_qa_email'
    )
    
    # 2. Daily Reminder email Monday-Sunday at 7:00 AM (on Saturday, only sends if plan exists)
    scheduler.add_job(
        daily_reminder_job,
        CronTrigger(day_of_week='mon-sun', hour=7, minute=0, timezone=TIMEZONE),
        id='daily_reminder_email'
    )
    
    scheduler.start()
    return scheduler
