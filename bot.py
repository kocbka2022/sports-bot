import os
import json
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage

BOT_TOKEN = os.environ.get("BOT_TOKEN")
ADMIN_IDS = [6141160793]

db_conn = sqlite3.connect("sports_bot.db", check_same_thread=False)
cursor = db_conn.cursor()

cursor.execute('''
    CREATE TABLE IF NOT EXISTS events (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT, description TEXT, options TEXT,
        status TEXT DEFAULT 'active', winner TEXT, created_at TIMESTAMP
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, event_id INTEGER, selected_option TEXT,
        bet_time TIMESTAMP, is_win BOOLEAN DEFAULT 0
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT, full_name TEXT, points INTEGER DEFAULT 0
    )
''')
db_conn.commit()

def add_event(title, description, options):
    cursor.execute("INSERT INTO events (title, description, options, status, created_at) VALUES (?, ?, ?, 'active', ?)",
                   (title, description, options, datetime.now()))
    db_conn.commit()

def get_active_events():
    cursor.execute("SELECT * FROM events WHERE status = 'active' ORDER BY created_at DESC")
    return cursor.fetchall()

def get_event(event_id):
    cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    return cursor.fetchone()

def finish_event(event_id, winner_option):
    cursor.execute("SELECT user_id, selected_option FROM bets WHERE event_id = ?", (event_id,))
    bets = cursor.fetchall()
    for user_id, selected in bets:
        if selected == winner_option:
            cursor.execute("UPDATE users SET points = points + 10 WHERE user_id = ?", (user_id,))
            cursor.execute("UPDATE bets SET is_win = 1 WHERE event_id = ? AND user_id = ?", (event_id, user_id))
    cursor.execute("UPDATE events SET status = 'finished', winner = ? WHERE id = ?", (winner_option, event_id))
    db_conn.commit()

def place_bet(user_id, event_id, selected_option):
    cursor.execute("SELECT id FROM bets WHERE user_id = ? AND event_id = ?", (user_id, event_id))
    if cursor.fetchone():
        return False
    cursor.execute("INSERT INTO bets (user_id, event_id, selected_option, bet_time) VALUES (?, ?, ?, ?)",
                   (user_id, event_id, selected_option, datetime.now()))
    db_conn.commit()
    return True

def register_user(user_id, username, full_name):
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username, full_name, points) VALUES (?, ?, ?, 0)",
                   (user_id, username, full_name))
    db_conn.commit()

def get_user_points(user_id):
    cursor.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else 0

def get_user_history(user_id):
    cursor.execute('''
        SELECT e.title, b.selected_option, b.is_win 
        FROM bets b JOIN events e ON b.event_id = e.id 
        WHERE b.user_id = ? ORDER BY b.bet_time DESC LIMIT 10
    ''', (user_id,))
    return cursor.fetchall()

def get_leaderboard():
    cursor.execute("SELECT full_name, username, points FROM users ORDER BY points DESC LIMIT 10")
    return cursor.fetchall()

storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)

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
    register_user(user.id, user.username, user.full_name)
    await message.answer(f"🏅 Добро пожаловать, {user.full_name}!\nДелай прогнозы и получай баллы!", reply_markup=main_keyboard)

@dp.message_handler(Text(equals="📋 Активные события"))
async def show_events(message: types.Message):
    events = get_active_events()
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
    event = get_event(event_id)
    if not event or event[4] != 'active':
        await callback.answer("Событие завершено!")
        return
    register_user(user_id, callback.from_user.username, callback.from_user.full_name)
    if place_bet(user_id, event_id, option):
        await callback.answer(f"Прогноз принят: {option}")
    else:
        await callback.answer("Вы уже делали прогноз!")

@dp.message_handler(Text(equals="🏆 Мой рейтинг"))
async def my_rating(message: types.Message):
    points = get_user_points(message.from_user.id)
    history = get_user_history(message.from_user.id)
    text = f"Ваши баллы: {points}\n\nИстория:\n"
    for title, opt, win in history:
        text += f"{title}: {opt} - {'✅' if win else '⏳'}\n"
    await message.answer(text)

@dp.message_handler(Text(equals="📊 Таблица лидеров"))
async def leaderboard(message: types.Message):
    leaders = get_leaderboard()
    text = "🏆 ТОП-10:\n"
    for i, (name, user, pts) in enumerate(leaders, 1):
        text += f"{i}. {name or user} — {pts} баллов\n"
    await message.answer(text)

@dp.message_handler(commands=['add_event'])
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
    add_event(data['title'], data['description'], json.dumps({opt: 1.0 for opt in opts}))
    await message.answer(f"Событие '{data['title']}' добавлено!")
    await state.finish()

@dp.message_handler(commands=['finish'])
async def finish_event_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer("Использование: /finish event_id winner")
        return
    _, event_id_str, winner = parts
    finish_event(int(event_id_str), winner)
    await message.answer(f"Событие завершено! Победитель: {winner}")

if __name__ == "__main__":
    executor.start_polling(dp, skip_updates=True)
