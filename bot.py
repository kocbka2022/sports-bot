import os
import json
import sqlite3
from datetime import datetime
from aiogram import Bot, Dispatcher, types
from aiogram.contrib.middlewares.logging import LoggingMiddleware
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, KeyboardButton, WebAppInfo, ReplyKeyboardMarkup
from aiogram.utils import executor
from aiogram.dispatcher.filters import Text
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.dispatcher import FSMContext
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.responses import HTMLResponse
import uvicorn
from threading import Thread

# ========== КОНФИГ ==========
BOT_TOKEN = "8769773881:AAEBc7dTnQV4itt2tjmoIjaFoI922V-LzT8"  # Замени на свой токен
ADMIN_IDS = [6141160793]  # Твой Telegram ID

# ========== БАЗА ДАННЫХ ==========
db_conn = sqlite3.connect("sports_bot.db", check_same_thread=False)
cursor = db_conn.cursor()

# Таблицы
cursor.execute('''
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
cursor.execute('''
    CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        event_id INTEGER,
        selected_option TEXT,
        bet_time TIMESTAMP,
        is_win BOOLEAN DEFAULT 0,
        points_earned INTEGER DEFAULT 0
    )
''')
cursor.execute('''
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        full_name TEXT,
        points INTEGER DEFAULT 0
    )
''')
db_conn.commit()

# ========== ФУНКЦИИ БАЗЫ ДАННЫХ ==========
def add_event(title, description, options):
    """options = {"Победа А": 3.5, "Ничья": 3.0, "Победа Б": 3.2}"""
    cursor.execute(
        "INSERT INTO events (title, description, options, status, created_at) VALUES (?, ?, ?, 'active', ?)",
        (title, description, json.dumps(options), datetime.now())
    )
    db_conn.commit()
    return cursor.lastrowid

def get_active_events():
    cursor.execute("SELECT * FROM events WHERE status = 'active' ORDER BY created_at DESC")
    return cursor.fetchall()

def get_all_events():
    cursor.execute("SELECT * FROM events ORDER BY created_at DESC")
    return cursor.fetchall()

def get_event(event_id):
    cursor.execute("SELECT * FROM events WHERE id = ?", (event_id,))
    return cursor.fetchone()

def finish_event(event_id, winner_option):
    """Начисляет баллы = 10 × коэффициент"""
    # Получаем коэффициенты события
    event = get_event(event_id)
    if not event:
        return 0
    options = json.loads(event[3])
    coefficient = options.get(winner_option, 1.0)
    points_to_add = int(10 * coefficient)
    
    # Начисляем баллы победителям
    cursor.execute("SELECT user_id, selected_option FROM bets WHERE event_id = ?", (event_id,))
    bets = cursor.fetchall()
    winners_count = 0
    for user_id, selected in bets:
        if selected == winner_option:
            cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points_to_add, user_id))
            cursor.execute("UPDATE bets SET is_win = 1, points_earned = ? WHERE event_id = ? AND user_id = ?", 
                          (points_to_add, event_id, user_id))
            winners_count += 1
    
    cursor.execute("UPDATE events SET status = 'finished', winner = ? WHERE id = ?", (winner_option, event_id))
    db_conn.commit()
    return winners_count, points_to_add

def place_bet(user_id, event_id, selected_option):
    cursor.execute("SELECT id FROM bets WHERE user_id = ? AND event_id = ?", (user_id, event_id))
    if cursor.fetchone():
        return False
    cursor.execute(
        "INSERT INTO bets (user_id, event_id, selected_option, bet_time) VALUES (?, ?, ?, ?)",
        (user_id, event_id, selected_option, datetime.now())
    )
    db_conn.commit()
    return True

def register_user(user_id, username, full_name):
    cursor.execute(
        "INSERT OR IGNORE INTO users (user_id, username, full_name, points) VALUES (?, ?, ?, 0)",
        (user_id, username, full_name)
    )
    db_conn.commit()

def get_user_points(user_id):
    cursor.execute("SELECT points FROM users WHERE user_id = ?", (user_id,))
    res = cursor.fetchone()
    return res[0] if res else 0

def get_user_history(user_id):
    cursor.execute('''
        SELECT e.title, b.selected_option, b.is_win, b.points_earned, e.options
        FROM bets b JOIN events e ON b.event_id = e.id 
        WHERE b.user_id = ? ORDER BY b.bet_time DESC LIMIT 20
    ''', (user_id,))
    result = []
    for row in cursor.fetchall():
        title, option, is_win, points, options_json = row
        options = json.loads(options_json)
        coefficient = options.get(option, 1.0)
        result.append({
            "title": title,
            "selected_option": option,
            "is_win": is_win,
            "points_earned": points,
            "coefficient": coefficient
        })
    return result

def get_leaderboard():
    cursor.execute("SELECT full_name, username, points FROM users ORDER BY points DESC LIMIT 10")
    return [{"name": row[0] or row[1] or f"User_{i}", "points": row[2]} for i, row in enumerate(cursor.fetchall())]

def get_user_bets_dict(user_id):
    cursor.execute("SELECT event_id, selected_option FROM bets WHERE user_id = ?", (user_id,))
    return {row[0]: row[1] for row in cursor.fetchall()}

def get_event_with_coef(event_id):
    event = get_event(event_id)
    if not event:
        return None
    options = json.loads(event[3])
    return {
        "id": event[0],
        "title": event[1],
        "description": event[2],
        "options": options,
        "status": event[4]
    }

# ========== ТЕЛЕГРАМ БОТ ==========
storage = MemoryStorage()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(bot, storage=storage)

# Кнопка Mini App
web_app_url = os.environ.get("RENDER_EXTERNAL_URL", "https://sports-bot.onrender.com") + "/miniapp"
web_app = WebAppInfo(url=web_app_url)

main_keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
main_keyboard.add(KeyboardButton("📋 Активные события"))
main_keyboard.add(KeyboardButton("🏆 Мой рейтинг"), KeyboardButton("📊 Таблица лидеров"))
main_keyboard.add(KeyboardButton("📱 Открыть прогнозы", web_app=web_app))

# Состояния для добавления событий
class AddEvent(StatesGroup):
    title = State()
    description = State()
    options = State()

# ========== ОБЩИЕ КОМАНДЫ ==========
@dp.message_handler(commands=['start'])
async def start(message: types.Message):
    user = message.from_user
    register_user(user.id, user.username, user.full_name)
    await message.answer(
        f"🏅 Добро пожаловать, {user.full_name}!\n\n"
        f"📊 Теперь прогнозы оцениваются с КОЭФФИЦИЕНТАМИ!\n\n"
        f"• Правильный прогноз приносит: 10 × коэффициент\n"
        f"• Пример: коэффициент 3.5 → +35 баллов\n\n"
        f"📱 Нажми '📱 Открыть прогнозы' для удобного интерфейса!",
        reply_markup=main_keyboard
    )

@dp.message_handler(Text(equals="📋 Активные события"))
async def show_events(message: types.Message):
    events = get_active_events()
    if not events:
        await message.answer("😔 Нет активных событий\n\nДобавь событие через /add_event")
        return
    
    for event in events:
        event_id, title, description, options_json, status, winner, created_at = event
        options = json.loads(options_json)
        
        # Формируем текст с коэффициентами
        options_text = "\n".join([f"  • {opt} — коэффициент {coef}" for opt, coef in options.items()])
        total_text = f"⚽️ *{title}*\n\n📝 {description}\n\n📊 *Варианты прогнозов:*\n{options_text}"
        
        # Кнопки с коэффициентами
        buttons = []
        for opt, coef in options.items():
            buttons.append([InlineKeyboardButton(f"{opt} (x{coef})", callback_data=f"bet_{event_id}_{opt}")])
        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
        
        await message.answer(total_text, parse_mode="Markdown", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith("bet_"))
async def place_bet_callback(callback: types.CallbackQuery):
    _, event_id_str, option = callback.data.split("_", 2)
    event_id = int(event_id_str)
    user_id = callback.from_user.id
    
    event = get_event(event_id)
    if not event or event[4] != 'active':
        await callback.answer("⏰ Это событие уже завершено!", show_alert=True)
        return
    
    register_user(user_id, callback.from_user.username, callback.from_user.full_name)
    
    if place_bet(user_id, event_id, option):
        options = json.loads(event[3])
        coef = options.get(option, 1.0)
        potential_points = int(10 * coef)
        await callback.answer(f"✅ Прогноз принят! {option} (x{coef})")
        await callback.message.answer(
            f"✨ *Твой прогноз принят!*\n\n"
            f"📋 Событие: *{event[1]}*\n"
            f"🔮 Твой выбор: *{option}* (коэффициент {coef})\n"
            f"💰 Потенциальный выигрыш: *{potential_points} баллов*",
            parse_mode="Markdown"
        )
    else:
        await callback.answer("⚠️ Ты уже делал прогноз на это событие!", show_alert=True)

@dp.message_handler(Text(equals="🏆 Мой рейтинг"))
async def my_rating(message: types.Message):
    user_id = message.from_user.id
    points = get_user_points(user_id)
    history = get_user_history(user_id)
    
    if not history:
        await message.answer(
            f"📊 *Твой счёт: {points} баллов*\n\n"
            f"📝 *История прогнозов:*\n"
            f"Пока нет завершённых прогнозов. Сделай свой первый прогноз!",
            parse_mode="Markdown"
        )
        return
    
    text = f"📊 *Твой счёт: {points} баллов*\n\n📝 *Последние прогнозы:*\n"
    for bet in history[:10]:
        if bet['is_win']:
            status = f"✅ +{bet['points_earned']} баллов (x{bet['coefficient']})"
        else:
            status = "⏳ ожидает результата"
        text += f"• *{bet['title']}*: {bet['selected_option']} — {status}\n"
    
    await message.answer(text, parse_mode="Markdown")

@dp.message_handler(Text(equals="📊 Таблица лидеров"))
async def leaderboard(message: types.Message):
    leaders = get_leaderboard()
    
    if not leaders:
        await message.answer("🏆 *Таблица лидеров*\n\nПока никого нет. Будь первым! 🚀", parse_mode="Markdown")
        return
    
    text = "🏆 *ТАБЛИЦА ЛИДЕРОВ* 🏆\n\n"
    for i, user in enumerate(leaders, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else f"{i}."
        text += f"{medal} *{user['name']}* — {user['points']} баллов\n"
    
    await message.answer(text, parse_mode="Markdown")

# ========== АДМИН-КОМАНДЫ ==========
@dp.message_handler(commands=['add_event'])
async def add_event_start(message: types.Message):
    if message.from_user.id in ADMIN_IDS:
        await AddEvent.title.set()
        await message.answer(
            "📝 *Добавление нового события*\n\n"
            "Введите *название* события:",
            parse_mode="Markdown"
        )

@dp.message_handler(state=AddEvent.title)
async def add_event_title(message: types.Message, state: FSMContext):
    await state.update_data(title=message.text)
    await AddEvent.next()
    await message.answer("📝 Введите *описание* события:", parse_mode="Markdown")

@dp.message_handler(state=AddEvent.description)
async def add_event_desc(message: types.Message, state: FSMContext):
    await state.update_data(description=message.text)
    await AddEvent.next()
    await message.answer(
        "📝 Введите *варианты прогнозов с коэффициентами*\n\n"
        "Формат: `Вариант1:коэффициент, Вариант2:коэффициент, ...`\n\n"
        "Пример: `Победа Реала:3.5, Ничья:3.0, Победа Баварии:3.2`\n\n"
        "Если коэффициент не указать, будет 1.0",
        parse_mode="Markdown"
    )

@dp.message_handler(state=AddEvent.options)
async def add_event_opts(message: types.Message, state: FSMContext):
    parts = [x.strip() for x in message.text.split(",")]
    options = {}
    
    for part in parts:
        if ":" in part:
            name, coef = part.split(":")
            options[name.strip()] = float(coef.strip())
        else:
            options[part] = 1.0
    
    if len(options) < 2:
        await message.answer("❌ Нужно минимум 2 варианта! Попробуй снова:", parse_mode="Markdown")
        return
    
    data = await state.get_data()
    event_id = add_event(data['title'], data['description'], options)
    
    # Формируем красивое сообщение
    options_text = "\n".join([f"  • {opt}: x{coef}" for opt, coef in options.items()])
    
    await message.answer(
        f"✅ *Событие успешно добавлено!*\n\n"
        f"📋 *Название:* {data['title']}\n"
        f"📝 *Описание:* {data['description']}\n"
        f"📊 *Варианты и коэффициенты:*\n{options_text}\n\n"
        f"🔢 ID события: `{event_id}`\n\n"
        f"Чтобы завершить событие: `/finish {event_id} Вариант`",
        parse_mode="Markdown"
    )
    await state.finish()

@dp.message_handler(commands=['finish'])
async def finish_event_cmd(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет прав администратора!")
        return
    
    parts = message.text.split()
    if len(parts) != 3:
        await message.answer(
            "📝 *Как завершить событие:*\n\n"
            "`/finish ID_события Победивший_вариант`\n\n"
            "Пример: `/finish 1 Победа Реала`\n\n"
            "*Важно:* Победивший вариант должен в точности совпадать с тем, который был в прогнозах!",
            parse_mode="Markdown"
        )
        return
    
    _, event_id_str, winner = parts
    event_id = int(event_id_str)
    
    event = get_event(event_id)
    if not event:
        await message.answer(f"❌ Событие с ID {event_id} не найдено!")
        return
    
    if event[4] != 'active':
        await message.answer(f"⚠️ Событие *{event[1]}* уже завершено!", parse_mode="Markdown")
        return
    
    options = json.loads(event[3])
    if winner not in options:
        await message.answer(
            f"❌ Вариант '{winner}' не найден в списке!\n\n"
            f"Доступные варианты: {', '.join(options.keys())}",
            parse_mode="Markdown"
        )
        return
    
    winners_count, points_awarded = finish_event(event_id, winner)
    
    await message.answer(
        f"✅ *Событие завершено!*\n\n"
        f"📋 *{event[1]}*\n"
        f"🏆 Победитель: *{winner}*\n"
        f"💰 Каждому победителю начислено: *{points_awarded} баллов* (10 × {options[winner]})\n"
        f"👥 Количество победителей: *{winners_count}*\n\n"
        f"🎉 Баллы автоматически добавлены в таблицу лидеров!",
        parse_mode="Markdown"
    )

@dp.message_handler(commands=['events_list'])
async def list_events(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    events = get_all_events()
    if not events:
        await message.answer("📭 Нет ни одного события")
        return
    
    text = "📋 *Список всех событий*\n\n"
    for event in events:
        event_id, title, description, options_json, status, winner, created_at = event
        status_emoji = "🟢" if status == "active" else "🔴"
        status_text = "активно" if status == "active" else f"завершено (победитель: {winner})"
        text += f"{status_emoji} *ID {event_id}:* {title} — {status_text}\n"
    
    await message.answer(text, parse_mode="Markdown")

# ========== FASTAPI ДЛЯ MINI APP ==========
app = FastAPI()
templates = Jinja2Templates(directory="templates")

@app.get("/miniapp", response_class=HTMLResponse)
async def miniapp(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/api/events")
async def get_events():
    events = get_active_events()
    # В реальном приложении user_id нужно получать из Telegram WebApp data
    user_id = 1  # Заглушка
    user_bets = get_user_bets_dict(user_id)
    return {
        "events": [{"id": e[0], "title": e[1], "description": e[2], "options": json.loads(e[3])} for e in events],
        "user_bets": user_bets
    }

@app.get("/api/rating")
async def get_rating():
    user_id = 1  # Заглушка
    return {
        "points": get_user_points(user_id),
        "history": get_user_history(user_id)
    }

@app.get("/api/leaders")
async def get_leaders():
    return {"leaders": get_leaderboard()}

@app.post("/api/bet")
async def api_place_bet(request: Request):
    data = await request.json()
    user_id = data.get("user_id")
    event_id = data.get("event_id")
    option = data.get("option")
    register_user(user_id, "", "")
    success = place_bet(user_id, event_id, option)
    return {"success": success, "error": None if success else "Уже делали прогноз"}

def run_fastapi():
    uvicorn.run(app, host="0.0.0.0", port=8000)

# ========== ЗАПУСК ==========
if __name__ == "__main__":
    # Запускаем FastAPI в отдельном потоке
    thread = Thread(target=run_fastapi, daemon=True)
    thread.start()
    # Запускаем бота
    print("🚀 Бот спортивных прогнозов с коэффициентами запущен!")
    print("📊 Правильный прогноз = 10 × коэффициент баллов")
    executor.start_polling(dp, skip_updates=True)
