import os
import asyncio
import json
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
import uvicorn

# ========== КОНФИГ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")  # Токен подтянется из настроек Render
ADMIN_IDS = [6141160793]  # ТВОЙ TELEGRAM ID (оставь как есть)
PORT = int(os.environ.get("PORT", 8000))
WEBHOOK_PATH = "/webhook"

# ========== БАЗА ДАННЫХ ==========
class Database:
    def __init__(self):
        self.conn = sqlite3.connect("sports_bot.db", check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT,
                description TEXT,
                options TEXT,
                status TEXT DEFAULT 'active',
                winner TEXT,
                created_at TIMESTAMP
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS bets (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                event_id INTEGER,
                selected_option TEXT,
                bet_time TIMESTAMP,
                is_win BOOLEAN DEFAULT 0
            )
        ''')
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                points INTEGER DEFAULT 0
            )
        ''')
        self.conn.commit()
    
    def add_event(self, title, description, options):
        self.cursor.execute(
            "INSERT INTO events (title, description, options, status, created_at) VALUES (?, ?, ?, 'active', ?)",
            (title, description, options, datetime.now())
        )
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_active_events(self):
        self.cursor.execute("SELECT * FROM events WHERE status = 'active' ORDER BY created_at DESC")
        return self.cursor.fetchall()
    
    def get_event(self, event_id):
        self.cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
        return self.cursor.fetchone()
    
    def finish_event(self, event_id, winner_option):
        self.cursor.execute("SELECT user_id, selected_option FROM bets WHERE event_id = ?", (event_id,))
        bets = self.cursor.fetchall()
        winners = 0
        for user_id, selected in bets:
            if selected == winner_option:
                self.cursor.execute("UPDATE users SET points = points + 10 WHERE user_id = ?", (user_id,))
                self.cursor.execute("UPDATE bets SET is_win = 1 WHERE event_id = ? AND user_id = ?", (event_id, user_id))
                winners += 1
        self.cursor.execute(
            "UPDATE events SET status = 'finished', winner = ? WHERE id = ?",
            (winner_option, event_id)
        )
        self.conn.commit()
        return winners
    
    def place_bet(self, user_id, event_id, selected_option):
        self.cursor.execute("SELECT id FROM bets WHERE user_id = ? AND event_id = ?", (user_id, event_id))
        if self.cursor.fetchone():
            return False
        self.cursor.execute(
            "INSERT INTO bets (user_id, event_id, selected_option, bet_time) VALUES (?, ?, ?, ?)",
            (user_id, event_id, selected_option, datetime.now())
        )
        self.conn.commit()
        return True
    
    def register_user(self, user_id, username, full_name):
        self.cursor.execute(
            "INSERT OR IGNORE INTO users (user_id, username, full_name, points) VALUES (?, ?, ?, 0)",
            (user_id, username, full_name)
        )
        self.conn.commit()
    
    def get_user_points(self, user_id):
        self.cursor.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
        res = self.cursor.fetchone()
        return res[0] if res else 0
    
    def get_user_history(self, user_id):
        self.cursor.execute('''
            SELECT e.title, b.selected_option, b.is_win 
            FROM bets b JOIN events e ON b.event_id = e.id 
            WHERE b.user_id = ? ORDER BY b.bet_time DESC LIMIT 10
        ''', (user_id,))
        return self.cursor.fetchall()
    
    def get_leaderboard(self):
        self.cursor.execute(
            "SELECT full_name, username, points FROM users ORDER BY points DESC LIMIT 10"
        )
        return self.cursor.fetchall()

db = Database()

# ========== КЛАВИАТУРЫ ==========
main_keyboard = types.ReplyKeyboardMarkup(
    keyboard=[
        [types.KeyboardButton(text="📋 Активные события")],
        [types.KeyboardButton(text="🏆 Мой рейтинг"), types.KeyboardButton(text="📊 Таблица лидеров")]
    ],
    resize_keyboard=True
)

# ========== СОСТОЯНИЯ ДЛЯ АДМИНА ==========
class AddEventStates(StatesGroup):
    title = State()
    description = State()
    options = State()

# ========== ИНИЦИАЛИЗАЦИЯ БОТА ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=storage)

# ========== ОБЩИЕ КОМАНДЫ ==========
@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    user = message.from_user
    db.register_user(user.id, user.username, user.full_name)
    await message.answer(
        f"🏅 *Добро пожаловать в систему спортивных прогнозов, {user.full_name}!*\n\n"
        f"📝 Делай прогнозы на спортивные события и получай баллы за правильные исходы!\n"
        f"🎯 Каждый правильный прогноз приносит *10 баллов*",
        parse_mode="Markdown",
        reply_markup=main_keyboard
    )

@dp.message(lambda message: message.text == "📋 Активные события")
async def show_events(message: types.Message):
    events = db.get_active_events()
    if not events:
        await message.answer("😔 *Активных событий пока нет*", parse_mode="Markdown")
        return
    for event in events:
        event_id, title, description, options_json, status, winner, created_at = event
        options = json.loads(options_json)
        buttons = [[InlineKeyboardButton(text=f"🔮 {opt}", callback_data=f"bet_{event_id}_{opt}")] for opt in options.keys()]
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(
            f"⚽️ *{title}*\n\n📝 {description}\n\n📊 *Варианты:* " + ", ".join(options.keys()),
            parse_mode="Markdown",
            reply_markup=keyboard
        )

@dp.callback_query(lambda c: c.data.startswith("bet_"))
async def place_bet(callback: types.CallbackQuery):
    _, event_id_str, selected_option = callback.data.split("_", 2)
    event_id = int(event_id_str)
    user_id = callback.from_user.id
    event = db.get_event(event_id)
    if not event or event[4] != 'active':
        await callback.answer("⏰ Событие завершено!", show_alert=True)
        return
    db.register_user(user_id, callback.from_user.username, callback.from_user.full_name)
    success = db.place_bet(user_id, event_id, selected_option)
    if success:
        await callback.answer(f"✅ Прогноз принят: {selected_option}")
        await callback.message.edit_reply_markup(reply_markup=None)
    else:
        await callback.answer("⚠️ Вы уже делали прогноз!", show_alert=True)

@dp.message(lambda message: message.text == "🏆 Мой рейтинг")
async def my_rating(message: types.Message):
    points = db.get_user_points(message.from_user.id)
    history = db.get_user_history(message.from_user.id)
    text = f"📊 *Ваш счет: {points} баллов*\n\n📝 *История:*\n"
    for title, option, is_win in history:
        status = "✅ +10" if is_win else "⏳ ожидает"
        text += f"• {title}: {option} — {status}\n"
    await message.answer(text, parse_mode="Markdown")

@dp.message(lambda message: message.text == "📊 Таблица лидеров")
async def leaderboard(message: types.Message):
    leaders = db.get_leaderboard()
    if not leaders:
        await message.answer("🏆 Таблица лидеров пуста", parse_mode="Markdown")
        return
    text = "🏆 *ТАБЛИЦА ЛИДЕРОВ*\n\n"
    for i, (full_name, username, points) in enumerate(leaders, 1):
        name = full_name or username or f"Игрок {i}"
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "📌"
        text += f"{medal} {i}. *{name}* — {points} баллов\n"
    await message.answer(text, parse_mode="Markdown")

# ========== АДМИН-ПАНЕЛЬ ==========
@dp.message(lambda message: message.text == "🔧 Админ-панель" and message.from_user.id in ADMIN_IDS)
async def admin_panel(message: types.Message):
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Добавить событие", callback_data="admin_add_event")],
        [InlineKeyboardButton(text="📋 Управление событиями", callback_data="admin_manage_events")]
    ])
    await message.answer("🔧 *Админ-панель*", parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query(lambda c: c.data == "admin_add_event" and c.from_user.id in ADMIN_IDS)
async def admin_add_event_start(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    await callback.message.answer("📝 Введите *название* события:", parse_mode="Markdown")
    await state.set_state(AddEventStates.title)
    await callback.answer()

@dp.message(AddEventStates.title)
async def add_event_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await message.answer("📝 Введите *описание*:", parse_mode="Markdown")
    await state.set_state(AddEventStates.description)

@dp.message(AddEventStates.description)
async def add_event_description(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await message.answer("📝 Введите *варианты* через запятую\nПример: `Победа А, Ничья, Победа Б`", parse_mode="Markdown")
    await state.set_state(AddEventStates.options)

@dp.message(AddEventStates.options)
async def add_event_options(message: types.Message, state: FSMContext):
    options_list = [opt.strip() for opt in message.text.split(",")]
    if len(options_list) < 2:
        await message.answer("❌ Нужно минимум 2 варианта!", parse_mode="Markdown")
        return
    options = {opt: 1.0 for opt in options_list}
    data = await state.get_data()
    db.add_event(data['title'], data['description'], json.dumps(options))
    await message.answer(f"✅ Событие *{data['title']}* добавлено!", parse_mode="Markdown")
    await state.clear()

@dp.callback_query(lambda c: c.data == "admin_manage_events" and c.from_user.id in ADMIN_IDS)
async def admin_manage_events(callback: types.CallbackQuery):
    events = db.cursor.execute("SELECT * FROM events ORDER BY created_at DESC").fetchall()
    for event in events:
        event_id, title, description, options_json, status, winner, created_at = event
        options = json.loads(options_json)
        status_emoji = "🟢" if status == "active" else "🔴"
        text = f"{status_emoji} *ID {event_id}: {title}*\n📊 Варианты: {', '.join(options.keys())}"
        if status == "active":
            buttons = [[InlineKeyboardButton(text="🏁 Завершить", callback_data=f"admin_finish_{event_id}")]]
            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
            await callback.message.answer(text, parse_mode="Markdown", reply_markup=keyboard)
        else:
            await callback.message.answer(f"{text}\n🏆 Победитель: {winner}", parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("admin_finish_") and c.from_user.id in ADMIN_IDS)
async def admin_finish_prompt(callback: types.CallbackQuery):
    event_id = int(callback.data.split("_")[2])
    event = db.get_event(event_id)
    options = json.loads(event[3])
    buttons = [[InlineKeyboardButton(text=f"🏆 {opt}", callback_data=f"admin_winner_{event_id}_{opt}")] for opt in options.keys()]
    keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
    await callback.message.answer(f"Выберите победителя для *{event[1]}*:", parse_mode="Markdown", reply_markup=keyboard)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("admin_winner_") and c.from_user.id in ADMIN_IDS)
async def admin_finish_event(callback: types.CallbackQuery):
    _, _, event_id_str, winner = callback.data.split("_", 3)
    event_id = int(event_id_str)
    winners = db.finish_event(event_id, winner)
    await callback.message.delete()
    await callback.message.answer(f"✅ Событие завершено! Победитель: *{winner}*. Начислено баллов: {winners}", parse_mode="Markdown")
    await callback.answer()

# ========== ЗАПУСК С ВЕБ-ХУКОМ ДЛЯ RENDER ==========
async def on_startup():
    webhook_url = f"{os.environ.get('RENDER_EXTERNAL_URL')}{WEBHOOK_PATH}"
    await bot.set_webhook(url=webhook_url, allowed_updates=types.Update.ALL_TYPES)
    print(f"Webhook set to {webhook_url}")

async def on_shutdown():
    await bot.delete_webhook()
    await bot.session.close()

async def handle_webhook(request: Request) -> Response:
    update = types.Update(**await request.json())
    await dp.feed_update(bot, update)
    return Response()

# Starlette app
app = Starlette(routes=[
    Route(WEBHOOK_PATH, handle_webhook, methods=["POST"]),
    Route("/health", lambda _: PlainTextResponse("OK"), methods=["GET"]),
])

@app.on_event("startup")
async def startup_event():
    await on_startup()

@app.on_event("shutdown")
async def shutdown_event():
    await on_shutdown()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=PORT)