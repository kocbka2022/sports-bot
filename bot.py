import os
import json
import sqlite3
import asyncio
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.requests import Request
from starlette.responses import Response, PlainTextResponse
from aiogram.dispatcher import Dispatcher as DP
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
import uvicorn

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [6141160793]

# ========== DATABASE ==========
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

# ========== BOT INIT ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = DP(bot, storage=storage)

main_keyboard = types.ReplyKeyboardMarkup(resize_keyboard=True)
main_keyboard.add("📋 Активные события")
main_keyboard.add("🏆 Мой рейтинг", "📊 Таблица лидеров")

class AddEvent(StatesGroup):
    title = State()
    description = State()
    options = State()

@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user = message.from_user
    db.register_user(user.id, user.username, user.full_name)
    await message.answer(
        f"🏅 Добро пожаловать, {user.full_name}!\nДелай прогнозы и получай баллы!",
        reply_markup=main_keyboard
    )

@dp.message_handler(Text(equals="📋 Активные события"))
async def show_events(message: types.Message):
    events = db.get_active_events()
    if not events:
        await message.answer("Нет активных событий")
        return
    for event in events:
        event_id, title, description, options_json, status, winner, created_at = event
        opts = json.loads(options_json)
        buttons = [[InlineKeyboardButton(opt, callback_data=f"bet_{event_id}_{opt}")] for opt in opts.keys()]
        kb = InlineKeyboardMarkup(inline_keyboard=buttons)
        await message.answer(f"⚽️ {title}\n{description}\nВарианты: {', '.join(opts.keys())}", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data.startswith("bet_"))
async def place_bet_callback(callback: types.CallbackQuery):
    _, event_id_str, option = callback.data.split("_", 2)
    event_id = int(event_id_str)
    user_id = callback.from_user.id
    event = db.get_event(event_id)
    if not event or event[4] != 'active':
        await callback.answer("Событие завершено!")
        return
    db.register_user(user_id, callback.from_user.username, callback.from_user.full_name)
    if db.place_bet(user_id, event_id, option):
        await callback.answer(f"Прогноз принят: {option}")
        await callback.message.edit_reply_markup()
    else:
        await callback.answer("Вы уже делали прогноз!")

@dp.message_handler(Text(equals="🏆 Мой рейтинг"))
async def my_rating(message: types.Message):
    points = db.get_user_points(message.from_user.id)
    history = db.get_user_history(message.from_user.id)
    text = f"Ваши баллы: {points}\n\nИстория:\n"
    for title, opt, win in history:
        text += f"{title}: {opt} - {'✅' if win else '⏳'}\n"
    await message.answer(text)

@dp.message_handler(Text(equals="📊 Таблица лидеров"))
async def leaderboard(message: types.Message):
    leaders = db.get_leaderboard()
    text = "🏆 ТОП-10:\n"
    for i, (name, user, pts) in enumerate(leaders, 1):
        text += f"{i}. {name or user} — {pts} баллов\n"
    await message.answer(text)

@dp.message_handler(Text(equals="🔧 Админ-панель"))
async def admin_panel(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await message.answer("Админ-панель\n/add_event - добавить событие")

@dp.message_handler(commands=['add_event'], state=None)
async def add_event_start(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await AddEvent.title.set()
        await message.answer("Введите название события")

@dp.message_handler(state=AddEvent.title)
async def add_event_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await AddEvent.next()
    await message.answer("Введите описание")

@dp.message_handler(state=AddEvent.description)
async def add_event_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await AddEvent.next()
    await message.answer("Введите варианты через запятую (например: Победа А, Ничья, Победа Б)")

@dp.message_handler(state=AddEvent.options)
async def add_event_opts(message: types.Message, state: FSMContext):
    opts = [x.strip() for x in message.text.split(",")]
    data = await state.get_data()
    db.add_event(data['title'], data['description'], json.dumps({opt: 1.0 for opt in opts}))
    await message.answer(f"Событие '{data['title']}' добавлено!")
    await state.finish()

async def on_startup(dp):
    WEBHOOK_URL = f"{os.environ.get('RENDER_EXTERNAL_URL')}/webhook"
    await bot.set_webhook(WEBHOOK_URL)
    print(f"Webhook set to {WEBHOOK_URL}")

app = Starlette(routes=[
    Route("/webhook", lambda r: Response(), methods=["POST"]),
    Route("/health", lambda r: PlainTextResponse("OK"), methods=["GET"]),
])

@app.on_event("startup")
async def startup():
    await on_startup(dp)

@app.on_event("shutdown")
async def shutdown():
    await bot.delete_webhook()

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 8000)))
