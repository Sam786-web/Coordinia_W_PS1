"""Real Telegram bot (python-telegram-bot v20+, async polling) running inside the FastAPI event loop.

Falls back to Simulator Mode (no crash) when TELEGRAM_BOT_TOKEN is missing/invalid or Telegram is unreachable.
Outbound relays / unblock / follow-up messages are sent by notifier.py via the Bot HTTP API, so they work
whether or not polling is running.
"""
import asyncio
from datetime import datetime, timezone
import html
import logging
import os
import re
import secrets
from pathlib import Path
from typing import Callable, Optional

from sqlmodel import Session, func, select
from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler,
                          filters)

import deadlines
import insights
import llm_engine
from database import engine
from models import AgentLog, Participant, Project, Task
from notifier import telegram_token

log = logging.getLogger("telegram_service")

PROOF_DIR = Path(__file__).resolve().parent / "uploads"
MAX_PROOF_BYTES = 15 * 1024 * 1024

STATUS_ICON = {"Pending": "⏳", "In Progress": "\U0001F527", "Blocked": "\U0001F6D1", "Complete": "✅"}
SLOT_CHOICES = {
    "now": ("\U0001F7E2 Free Now", "Available now"),
    "30m": ("\U0001F552 Free in 30m", "Free in 30 mins"),
    "6pm": ("\U0001F555 Free at 6 PM", "Free at 6:00 PM"),
    "async": ("\U0001F4AC Async Only", "Async only"),
}


class TelegramService:
    def __init__(self) -> None:
        self.app: Optional[Application] = None
        self.mode = "simulator"
        self.reason: Optional[str] = "not started"
        self.bot_username: Optional[str] = None
        # Hooks injected by main.py
        self.handle_text: Optional[Callable[..., dict]] = None  # (participant_id, text, task_id=None) -> agent result
        self.emit: Optional[Callable[[str], None]] = None  # broadcast STATE_UPDATED
        # Messages waiting for "which task?" answers: key -> (participant_id, chat user id, text)
        self.pending: dict[str, tuple[int, int, str]] = {}
        self.pending_files: dict[str, tuple[int, int, str, str]] = {}  # key -> (participant, telegram user, file, caption)
        self.handle_file: Optional[Callable[..., dict]] = None
        self.handle_request: Optional[Callable[..., dict]] = None
        self.pending_requests: dict[str, tuple[int, int, int, str]] = {}  # key -> (requester, telegram user, assignee, text)

    # ---------- lifecycle ----------

    async def start(self, handle_text: Callable[[int, str], dict], emit: Callable[[str], None],
                    handle_file: Optional[Callable[..., dict]] = None,
                    handle_request: Optional[Callable[..., dict]] = None) -> None:
        self.handle_text, self.emit, self.handle_file = handle_text, emit, handle_file
        self.handle_request = handle_request
        token = telegram_token()
        if os.getenv("TELEGRAM_DISABLE_POLLING", "").strip().lower() in ("1", "true", "yes"):
            self.reason = "TELEGRAM_DISABLE_POLLING is set"
        elif not token:
            self.reason = "TELEGRAM_BOT_TOKEN not set"
        else:
            try:
                app = Application.builder().token(token).build()
                app.add_handler(CommandHandler("start", self.cmd_start))
                app.add_handler(CommandHandler("auth", self.cmd_auth))
                app.add_handler(CommandHandler("join", self.cmd_join))
                app.add_handler(CommandHandler("status", self.cmd_status))
                app.add_handler(CommandHandler("slot", self.cmd_slot))
                app.add_handler(CommandHandler(["end", "logout"], self.cmd_end))
                app.add_handler(CommandHandler("request", self.cmd_request))
                app.add_handler(CallbackQueryHandler(self.on_request_pick, pattern=r"^req:"))
                app.add_handler(CallbackQueryHandler(self.on_slot_click, pattern=r"^slot:"))
                app.add_handler(CallbackQueryHandler(self.on_task_pick, pattern=r"^pick:"))
                app.add_handler(CallbackQueryHandler(self.on_proof_pick, pattern=r"^proof:"))
                app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, self.on_file))
                app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_text))
                app.add_error_handler(self.on_error)
                await asyncio.wait_for(app.initialize(), timeout=15)  # validates the token (getMe)
                await app.start()
                await app.updater.start_polling(drop_pending_updates=True)
                try:  # the menu Telegram shows when you type "/"
                    await app.bot.set_my_commands([
                        BotCommand("start", "Welcome and your tasks"),
                        BotCommand("auth", "Verify with your 6-digit PIN"),
                        BotCommand("status", "Your team's task board"),
                        BotCommand("slot", "Share when you're free"),
                        BotCommand("request", "Ask the Team Lead to add a task"),
                        BotCommand("end", "Sign out, to verify again"),
                    ])
                except Exception as exc:  # cosmetic: never block the bot over the menu
                    log.info("Couldn't set the bot command menu: %s", exc)
                self.app, self.mode, self.reason = app, "live", None
                self.bot_username = app.bot.username
                log.info("Telegram bot @%s is live (polling)", self.bot_username)
                return
            except Exception as exc:  # InvalidToken, NetworkError, TimedOut...
                self.reason = f"{type(exc).__name__}: {str(exc)[:160]}"
        log.warning("Telegram running in Simulator Mode: %s", self.reason)

    async def stop(self) -> None:
        if not self.app:
            return
        try:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            await self.app.stop()
            await self.app.shutdown()
        except Exception as exc:
            log.warning("Telegram shutdown: %s", exc)
        self.app, self.mode = None, "simulator"

    def status(self) -> dict:
        # Personal links carry a secret PIN, so they are only shown to the Team Lead in the dashboard
        return {"mode": self.mode, "reason": self.reason, "bot_username": self.bot_username}

    def _changed(self, reason: str) -> None:
        if self.emit:
            self.emit(reason)

    # ---------- commands ----------

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        arg = context.args[0] if context.args else ""
        if arg.lower().startswith("auth_"):  # deep link from the dashboard: /start auth_482913
            await self._authenticate(update, arg[5:])
            return
        if arg.lower().startswith("inv_"):  # invite deep link: /start inv_SMART-4821_Aarav (new teammates)
            code, _, name = arg[4:].partition("_")
            await self._invite_join(update, code, name.replace("_", " "))
            return
        if arg.lower().startswith("join_"):  # old name-only links are no longer trusted
            await update.effective_message.reply_text(_AUTH_HELP)
            return
        await update.effective_message.reply_html(start_message(update.effective_user.id), disable_web_page_preview=True)

    async def cmd_auth(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not context.args:
            await update.effective_message.reply_text(_AUTH_HELP)
            return
        await self._authenticate(update, context.args[0])

    async def cmd_join(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """'/join CODE Name' lets a NEW teammate join with an invite code; existing members verify with /auth."""
        parts = context.args or []
        if len(parts) >= 2 and re.fullmatch(r"[A-Za-z]+-[A-Za-z0-9]+", parts[0]):
            await self._invite_join(update, parts[0], " ".join(parts[1:]))
            return
        await update.effective_message.reply_text(_AUTH_HELP)

    async def cmd_end(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/end: unlink this Telegram account from every member it verified as, so verification starts over."""
        user = update.effective_user
        with Session(engine) as s:
            linked = s.exec(select(Participant).where(Participant.telegram_chat_id == str(user.id))).all()
            for person in linked:
                person.telegram_chat_id, person.is_verified = None, False
                _log(s, person.project_id, "REPLY", f"{person.name} signed out of Telegram with /end (@{user.username or user.id})")
            s.commit()
            names = sorted({p.name for p in linked})
        # forget any "which task?" question still waiting for this user
        for key in [k for k, v in self.pending.items() if v[1] == user.id]:
            self.pending.pop(key, None)
        if names:
            self._changed("TELEGRAM_END")
            text = (f"\U0001F44B Signed out: this Telegram account is no longer verified as {', '.join(names)}. "
                    "Messages here won't update any tasks now.\n\n"
                    "To verify again (or as someone else), send your 6-digit PIN, e.g. /auth 482913.")
        else:
            text = "This Telegram account isn't verified as anyone yet. Send your 6-digit PIN to verify, e.g. /auth 482913."
        await update.effective_message.reply_text(text)

    def _bind(self, s: Session, person: Participant, user) -> None:
        """Bind this Telegram account to one member per project (unlinking it from anyone else there)."""
        for other in s.exec(select(Participant).where(Participant.project_id == person.project_id,
                                                      Participant.telegram_chat_id == str(user.id))).all():
            if other.id != person.id:
                other.telegram_chat_id, other.is_verified = None, False
        person.telegram_chat_id = str(user.id)
        person.telegram_username = user.username
        person.is_verified = True
        person.telegram_linked_at = datetime.now(timezone.utc)  # this project is now the chat's active one

    async def _authenticate(self, update: Update, pin: str) -> None:
        user = update.effective_user
        pin = re.sub(r"[\s-]", "", pin or "").upper()  # "482 913" or "482-913" both work
        with Session(engine) as s:
            person = s.exec(select(Participant).where(func.upper(Participant.auth_pin) == pin)).first() if pin else None
            if not person:
                await update.effective_message.reply_text(
                    "That PIN wasn't recognised. Check it with your Team Lead and send /auth <PIN> again.")
                return
            self._bind(s, person, user)
            project = s.get(Project, person.project_id)
            tasks = s.exec(select(Task).where(Task.owner_id == person.id).order_by(Task.id)).all()
            _log(s, person.project_id, "REPLY", f"{person.name} verified on Telegram with their PIN (@{user.username or user.id})")
            s.commit()
            listing = "\n".join(f"• {t.title} ({t.status})" for t in tasks) or "• No tasks yet"
            others = [s.get(Project, o.project_id) for o in s.exec(select(Participant).where(
                Participant.telegram_chat_id == str(user.id), Participant.id != person.id)).all()]
            others = sorted({o.name for o in others if o})
            text = (f"✅ Verified! You are {person.name} ({person.role or 'team member'}) in \"{project.name}\".\n"
                    f"Your tasks:\n{listing}\n\nOnly you can update these. Just reply with your progress.")
            if others:
                text += (f"\n\nYour messages now update \"{project.name}\". You're also verified in "
                         f"{', '.join(chr(34) + o + chr(34) for o in others)}: send that project's PIN to switch to it.")
        self._changed("TELEGRAM_AUTH")
        await update.effective_message.reply_text(text)

    async def _invite_join(self, update: Update, code: str, name: str) -> None:
        user = update.effective_user
        name = name.strip()
        with Session(engine) as s:
            project = s.exec(select(Project).where(Project.invite_code == code.strip().upper())).first()
            if not project or not name:
                await update.effective_message.reply_text("That invite code wasn't found. Usage: /join <INVITE-CODE> <Your name>")
                return
            taken = s.exec(select(Participant).where(Participant.project_id == project.id,
                                                     func.lower(Participant.name) == name.lower())).first()
            if taken:
                await update.effective_message.reply_text(
                    f"{taken.name} is already on this team. If that's you, verify with your personal PIN: /auth <PIN>")
                return
            person = Participant(project_id=project.id, name=name, role="Team member")
            s.add(person)
            s.flush()  # assigns the personal PIN
            self._bind(s, person, user)
            _log(s, project.id, "REPLY", f"{person.name} joined \"{project.name}\" from Telegram with the invite code")
            s.commit()
            text = (f"✅ Welcome to \"{project.name}\", {person.name}! You're verified on this chat.\n"
                    f"Your personal PIN is {person.auth_pin} - use it with the invite code to sign in to the app.")
        self._changed("TELEGRAM_JOIN")
        await update.effective_message.reply_text(text)

    async def cmd_status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        with Session(engine) as s:
            person = _participant_by_user(s, update.effective_user.id)
            project_id = person.project_id if person else None
        if not project_id:  # a team's board is only for its verified members
            await update.effective_message.reply_text(_AUTH_HELP)
            return
        await update.effective_message.reply_html(status_board_html(project_id), disable_web_page_preview=True)

    async def cmd_slot(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        buttons = [[InlineKeyboardButton(SLOT_CHOICES[k][0], callback_data=f"slot:{k}") for k in ("now", "30m")],
                   [InlineKeyboardButton(SLOT_CHOICES[k][0], callback_data=f"slot:{k}") for k in ("6pm", "async")]]
        await update.effective_message.reply_text("\U0001F4C5 When are you free for a quick 15-min sync?",
                                                  reply_markup=InlineKeyboardMarkup(buttons))

    async def on_slot_click(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        choice = SLOT_CHOICES.get(query.data.split(":", 1)[1])
        user = update.effective_user
        with Session(engine) as s:
            person = _participant_by_user(s, user.id, user.username)
            if not person or not choice:
                await query.answer("Verify first: send /auth <your PIN>", show_alert=True)
                return
            person.availability = llm_engine.availability_for_storage(choice[1])
            _log(s, person.project_id, "REPLY", f"{person.name} via telegram: availability -> {person.availability}")
            plan = llm_engine.coordinate_team_slots(
                [p.model_dump() for p in _participants(s, person.project_id)],
                [t.model_dump() for t in s.exec(select(Task).where(Task.project_id == person.project_id)).all()])
            s.commit()
            name, availability = person.name, person.availability
        await query.answer(f"Saved: {availability}")
        slot = plan["recommended_slot"]
        suggestion = (f"\n\U0001F91D Best {plan['purpose'].lower()}: {slot['label']} with {', '.join(plan['attendees'])}"
                      if slot else "")
        await query.edit_message_text(f"✅ {name} is {availability}.{suggestion}")
        self._changed("TELEGRAM_SLOT")

    # ---------- natural-language updates ----------

    async def on_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        msg = update.effective_message
        if not msg or not msg.text:
            return
        user, text = update.effective_user, msg.text.strip()
        is_private = update.effective_chat.type == "private"
        is_pin = bool(re.fullmatch(r"\d{3}[\s-]?\d{3}", text))  # just a 6-digit PIN on its own
        with Session(engine) as s:
            person = _participant_by_user(s, user.id)
            pid = person.id if person else None
            pin_known = is_pin and s.exec(select(Participant).where(Participant.auth_pin == re.sub(r"[\s-]", "", text))).first()
        if is_private and pin_known:  # verify, or switch to another project with that project's PIN
            await self._authenticate(update, text)
            return
        if not pid:  # only PIN-verified accounts can update tasks
            if is_private and is_pin:
                await self._authenticate(update, text)
            elif is_private:
                await msg.reply_text(_AUTH_HELP)
            return
        await self._run_update(msg, user.id, pid, text)

    async def _run_update(self, msg, user_id: int, pid: int, text: str, task_id: Optional[int] = None, edit=None) -> None:
        reply = edit or msg.reply_text
        try:
            out = await asyncio.to_thread(self.handle_text, pid, text, task_id)
        except Exception as exc:
            log.exception("Failed to process Telegram message")
            await reply(f"⚠️ I couldn't process that update ({type(exc).__name__}). Please try again.")
            return
        r = out["result"]
        if out.get("denied"):
            await reply(out["denied"])
            return
        self._changed("TELEGRAM_MESSAGE")
        if r.get("needs_task_choice"):  # generic "done" with several open tasks: let them pick
            key = secrets.token_hex(4)
            self.pending[key] = (pid, user_id, text)
            buttons = [[InlineKeyboardButton(o["title"][:60], callback_data=f"pick:{key}:{o['id']}")] for o in r["task_options"]]
            await msg.reply_text(r["reply_to_sender"].split("\n")[0], reply_markup=InlineKeyboardMarkup(buttons))
            return
        extra = []
        if r.get("auto_relay_target_name"):
            extra.append(f"\U0001F4E8 Relayed to {r['auto_relay_target_name']}")
        if out.get("unblocked_task"):
            extra.append(f"\U0001F513 Unblocked \"{out['unblocked_task']['title']}\"")
        await reply(r["reply_to_sender"] + (("\n" + " • ".join(extra)) if extra else ""))

    async def on_task_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        _, key, task_id = query.data.split(":", 2)
        pending = self.pending.get(key)
        if not pending:
            await query.answer("This question has expired. Send your update again.", show_alert=True)
            return
        pid, owner_user_id, text = pending
        if update.effective_user.id != owner_user_id:  # in group chats, only the sender may answer
            await query.answer("Only the person who sent the update can choose.", show_alert=True)
            return
        self.pending.pop(key, None)
        await query.answer()
        await self._run_update(query.message, owner_user_id, pid, text, int(task_id), edit=query.edit_message_text)

    # ---------- proof of work: photos & files ----------

    async def on_file(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """A photo or document from a verified member is saved and attached to their task as proof."""
        msg, user = update.effective_message, update.effective_user
        with Session(engine) as s:
            person = _participant_by_user(s, user.id)
            pid = person.id if person else None
        if not pid:
            await msg.reply_text(_AUTH_HELP)
            return
        if msg.photo:
            media, ext = msg.photo[-1], ".jpg"  # largest size
        else:
            media = msg.document
            ext = Path(media.file_name or "").suffix.lower()[:8] or ".bin"
            if (media.file_size or 0) > MAX_PROOF_BYTES:
                await msg.reply_text("That file is too big (max 15 MB). Send a smaller file or a link to it.")
                return
        if not re.fullmatch(r"\.[A-Za-z0-9]{1,7}", ext):
            ext = ".bin"
        name = secrets.token_urlsafe(18).replace("-", "x") + ext
        try:
            PROOF_DIR.mkdir(exist_ok=True)
            tg_file = await context.bot.get_file(media.file_id)
            await tg_file.download_to_drive(PROOF_DIR / name)
        except Exception as exc:
            log.warning("Proof file download failed: %s", exc)
            await msg.reply_text("I couldn't download that file (network problem). Please send it again in a moment.")
            return
        await self._attach_file(msg.reply_text, pid, user.id, name, msg.caption or "")

    async def _attach_file(self, reply, pid: int, user_id: int, name: str, caption: str, task_id: Optional[int] = None) -> None:
        if not self.handle_file:
            await reply("Saving proof files isn't available right now.")
            return
        out = await asyncio.to_thread(self.handle_file, pid, name, caption, task_id)
        if out.get("choose"):
            key = secrets.token_hex(4)
            self.pending_files[key] = (pid, user_id, name, caption)
            buttons = [[InlineKeyboardButton(c["title"][:60], callback_data=f"proof:{key}:{c['id']}")] for c in out["choose"][:8]]
            await reply("📎 Got your file. Which task is it proof for?", reply_markup=InlineKeyboardMarkup(buttons))
            return
        self._changed("TELEGRAM_PROOF")
        if out["status"] == "Complete":
            text = (f"📎 Got it: attached to \"{out['task']}\" as proof. "
                    "Your Team Lead can see it on the dashboard now.")
        else:
            text = (f"📎 Saved to \"{out['task']}\" ({out['status']}). "
                    "Tell me when it's done and it will count as proof.")
        await reply(text)

    # ---------- task requests ----------

    async def cmd_request(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        """/request MITAL: share the test credentials - for the login tests
        Asks the Team Lead to add a task for a teammate; buttons place it before / after one of your tasks."""
        msg, user = update.effective_message, update.effective_user
        text = " ".join(context.args or []).strip()
        with Session(engine) as s:
            me = _participant_by_user(s, user.id)
            if not me:
                await msg.reply_text(_AUTH_HELP)
                return
            team = _participants(s, me.project_id)
            mine = [t for t in s.exec(select(Task).where(Task.owner_id == me.id).order_by(Task.id)).all() if t.status != "Complete"]
            names = ", ".join(p.name for p in team if p.id != me.id) or "a teammate"
            if not text:
                await msg.reply_text(
                    "Ask your Team Lead to add a task:\n/request NAME: what needs doing - extra details\n"
                    f"e.g. /request {next((p.name for p in team if p.id != me.id), 'MITAL')}: share the test credentials - "
                    f"needed for the login tests\n\nTeammates: {names} (or 'me' for yourself)")
                return
            m = re.match(r"@?([^:,]+?)\s*[:,-]\s*(.+)$", text) or re.match(r"@?(\S+)\s+(.+)$", text)
            who, what = (m.group(1).strip(), m.group(2).strip()) if m else ("", text)
            if who.lower() in ("me", "myself", "i"):
                assignee = me
            else:
                assignee = next((p for p in team if p.name.lower() == who.lower()), None)
            if not assignee or len(what) < 3:
                await msg.reply_text(f"Who should do it? Start with a teammate's name: {names}.\n"
                                     "e.g. /request MITAL: share the test credentials")
                return
            options = [(t.id, t.title) for t in mine[:3]]
            assignee_id, assignee_name = assignee.id, assignee.name
        key = secrets.token_hex(4)
        self.pending_requests[key] = (me.id, user.id, assignee_id, what)
        buttons = []
        for tid, title in options:
            buttons.append([InlineKeyboardButton(f"Before \"{title[:28]}\"", callback_data=f"req:{key}:before:{tid}")])
            buttons.append([InlineKeyboardButton(f"After \"{title[:28]}\"", callback_data=f"req:{key}:after:{tid}")])
        buttons.append([InlineKeyboardButton("No particular order", callback_data=f"req:{key}:none:0")])
        await msg.reply_text(f"\U0001F4DD Request for {assignee_name}: \"{what}\"\nWhen is it needed?",
                             reply_markup=InlineKeyboardMarkup(buttons))

    async def on_request_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        _, key, relation, tid = query.data.split(":", 3)
        pending = self.pending_requests.get(key)
        if not pending:
            await query.answer("This request has expired. Send /request again.", show_alert=True)
            return
        pid, owner_user_id, assignee_id, what = pending
        if update.effective_user.id != owner_user_id:
            await query.answer("Only the person who made the request can choose.", show_alert=True)
            return
        self.pending_requests.pop(key, None)
        await query.answer()
        if not self.handle_request:
            await query.edit_message_text("Task requests aren't available right now.")
            return
        try:
            r = await asyncio.to_thread(self.handle_request, pid, assignee_id, what, relation, int(tid) or None)
        except Exception as exc:
            await query.edit_message_text(f"Couldn't send the request: {getattr(exc, 'detail', exc)}")
            return
        self._changed("TASK_REQUEST")
        when = {"before": f"before \"{r['related_task_title']}\"", "after": f"after \"{r['related_task_title']}\""}.get(r["relation"], "")
        await query.edit_message_text(
            f"\u2705 Sent to your Team Lead: \"{r['title']}\" for {r['assignee_name']}" + (f", {when}" if when else "")
            + ". I'll let you know when they decide.")

    async def on_proof_pick(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        query = update.callback_query
        _, key, task_id = query.data.split(":", 2)
        pending = self.pending_files.get(key)
        if not pending:
            await query.answer("This question has expired. Send the file again.", show_alert=True)
            return
        pid, owner_user_id, name, caption = pending
        if update.effective_user.id != owner_user_id:
            await query.answer("Only the person who sent the file can choose.", show_alert=True)
            return
        self.pending_files.pop(key, None)
        await query.answer()
        await self._attach_file(query.edit_message_text, pid, owner_user_id, name, caption, int(task_id))

    async def on_error(self, update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
        log.warning("Telegram error: %s", context.error)


# ---------- DB helpers ----------

def _active_project(s: Session) -> Optional[Project]:
    return s.exec(select(Project).where(Project.is_active == True)).first() or \
        s.exec(select(Project).order_by(Project.id)).first()  # noqa: E712


def _participants(s: Session, project_id: Optional[int] = None) -> list[Participant]:
    if project_id is None:
        project = _active_project(s)
        if not project:
            return []
        project_id = project.id
    return s.exec(select(Participant).where(Participant.project_id == project_id).order_by(Participant.id)).all()


def start_message(telegram_user_id: int) -> str:
    """/start: a verified member sees their own project and tasks; anyone else only how to verify.

    No project or team details are shown to someone who hasn't proved who they are with their PIN."""
    with Session(engine) as s:
        person = _participant_by_user(s, telegram_user_id)
        if not person:
            return "\n".join([
                "\U0001F44B <b>Hi! I'm Coordina, your team's AI project coordinator.</b>", "",
                "<b>Verify yourself:</b> send your 6-digit PIN, e.g. <code>/auth 482913</code> (or just the 6 digits).",
                "Your Team Lead has your personal PIN, or a 1-tap link that does this for you.", "",
                "Once verified, you'll see your project and tasks here and can send updates in plain English or Hinglish."])
        project = s.get(Project, person.project_id)
        mine = s.exec(select(Task).where(Task.owner_id == person.id).order_by(Task.id)).all()
        lines = [f"\U0001F44B <b>Hi {html.escape(person.name)}!</b> You're verified on "
                 f"<b>{html.escape(project.name if project else 'your project')}</b>.", "", "<b>Your tasks:</b>"]
        for t in mine:
            due = f" • due {html.escape(deadlines.humanize(t.deadline))}" if t.deadline else ""
            lines.append(f"{STATUS_ICON.get(t.status, '•')} {html.escape(t.title)} ({t.status}){due}")
        if not mine:
            lines.append("No tasks assigned to you yet.")
        lines += ["", "Message me updates in plain English or Hinglish, e.g.",
                  "<i>\"API is connected, but waiting for test credentials from Meera\"</i>",
                  "", "/status - your team's task board • /slot - share when you're free",
                  "/request - ask your Team Lead to add a task for a teammate",
                  "/end - sign out, to verify again (or as someone else)"]
        return "\n".join(lines)


_AUTH_HELP = ("To update your tasks, verify yourself first: send /auth <your PIN> (for example /auth 482913, or just send the 6 digits). "
              "Your Team Lead can give you the PIN, or a 1-tap link that does it for you.")


def _participant_by_user(s: Session, user_id: int, username: Optional[str] = None) -> Optional[Participant]:
    """The member this Telegram account is bound to (PIN-verified). In several projects, the one picked last with /auth."""
    linked = s.exec(select(Participant).where(Participant.telegram_chat_id == str(user_id),
                                              Participant.is_verified == True)).all()  # noqa: E712
    if not linked:
        return None
    floor = datetime.min.replace(tzinfo=timezone.utc)
    return max(linked, key=lambda p: (_aware(p.telegram_linked_at) or floor, p.id))


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    return dt.replace(tzinfo=timezone.utc) if dt and dt.tzinfo is None else dt


def _log(s: Session, project_id: int, log_type: str, summary: str) -> None:
    s.add(AgentLog(project_id=project_id, log_type=log_type, summary=summary))


def status_board_html(project_id: Optional[int] = None) -> str:
    with Session(engine) as s:
        project = s.get(Project, project_id) if project_id else _active_project(s)
        if not project:
            return "No active project."
        people = {p.id: p.name for p in _participants(s, project.id)}
        tasks = insights.work_order(s.exec(select(Task).where(Task.project_id == project.id).order_by(Task.id)).all())
        by_id = {t.id: t for t in tasks}
        done = sum(t.status == "Complete" for t in tasks)
        lines = [f"\U0001F4CB <b>{html.escape(project.name)}</b>", f"{done}/{len(tasks)} complete", ""]
        for t in tasks:
            icon = STATUS_ICON.get(t.status, "•")
            line = (f"{icon} <b>{html.escape(t.title)}</b>\n"
                    f"     {html.escape(people.get(t.owner_id, 'Unassigned'))} • {t.status}")
            if t.deadline:
                line += f" • due {html.escape(deadlines.humanize(t.deadline))}"
            up = by_id.get(t.depends_on_task_id)
            if up and up.status == "Blocked":
                line += f"\n     ⚠️ At risk: depends on blocked \"{html.escape(up.title)}\""
            if t.status == "Blocked":
                who = people.get(t.blocked_by_participant_id)
                line += f"\n     \U0001F6D1 {html.escape(t.blocker_reason or 'Blocked')}" + (f" (needs {html.escape(who)})" if who else "")
            lines.append(line)
    return "\n".join(lines)


telegram_service = TelegramService()
