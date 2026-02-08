import asyncio
import logging
import json
import aiosqlite
import random
from datetime import datetime, time, timedelta

from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton, 
    InputMediaPhoto
)

# --- НАСТРОЙКИ ---
TOKEN = "8505098635:AAGkM2qizQkil7Lfoy3OgjYVsS320APY5HQ"
ADMIN_ID = 7467909699
DB_NAME = "cozy_dating.db"

# Инициализация
bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# Глобальная очередь для свиданий вслепую
BLIND_DATE_QUEUE = {} 

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Основная таблица пользователей
        # tea_pref оставляем в структуре, чтобы не ломать старые БД, но использовать не будем
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                age INTEGER,
                gender TEXT,
                interested_in TEXT,
                city TEXT,
                bio TEXT,
                qotd_answer TEXT,
                content_ids TEXT,
                content_type TEXT,
                tea_pref TEXT, 
                search_video_only INTEGER DEFAULT 0,
                is_active INTEGER DEFAULT 1,
                is_banned INTEGER DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                report_count INTEGER DEFAULT 0,
                quiet_mode INTEGER DEFAULT 0,
                mood_today TEXT,
                last_active DATETIME,
                reg_date DATETIME,
                voice_id TEXT
            )
        """)
        
        try:
            await db.execute("ALTER TABLE users ADD COLUMN voice_id TEXT")
        except:
            pass 

        await db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                from_id INTEGER,
                to_id INTEGER,
                reaction TEXT,
                timestamp DATETIME,
                UNIQUE(from_id, to_id)
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        await db.execute("INSERT OR IGNORE INTO config (key, value) VALUES ('qotd', 'Твоя суперспособность в реальной жизни?')")
        
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
def get_profile_link(user_id, username, name):
    if username:
        return f"@{username}"
    return f"<a href='tg://user?id={user_id}'>{name}</a>"

def is_quiet_hours():
    """Проверка ночного времени (00:00 - 08:00)"""
    now = datetime.now().time()
    return time(0, 0) <= now < time(8, 0)

async def get_qotd():
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT value FROM config WHERE key='qotd'") as c:
            res = await c.fetchone()
            return res[0] if res else "Как дела?"

async def send_user_profile(chat_id, user_data, is_match=False, match_with_me=False, admin_view=False):
    """
    Универсальная функция отправки анкеты
    """
    if not user_data: return

    # Распаковка
    uid = user_data[0]
    username = user_data[1]
    name = user_data[2]
    age = user_data[3]
    city = user_data[6]
    bio = user_data[7]
    qotd_ans = user_data[8]
    content_ids_raw = user_data[9]
    c_type = user_data[10]
    # tea_pref = user_data[11] (Игнорируем)
    quiet = user_data[17] if len(user_data) > 17 else 0
    voice_id = user_data[21] if len(user_data) > 21 else None
    
    # Декодинг медиа
    try:
        media_files = json.loads(content_ids_raw)
        if not isinstance(media_files, list): media_files = [content_ids_raw]
    except: media_files = []

    # Тексты
    qotd_text = await get_qotd()
    
    kb_markup = None

    if is_match:
        header = f"💖 <b>ЭТО ВЗАИМНО!</b>\nКонтакт: {get_profile_link(uid, username, name)}\n"
        header += f"\n🎲 <b>Тема для старта:</b>\n<i>«{qotd_text}»</i>\nСпроси, что {name} думает об этом!"
    elif admin_view:
        header = f"🕵️ <b>Админ-просмотр:</b> {name}, {age}\nID: `{uid}`"
        kb_markup = get_admin_action_kb(uid)
    else:
        header = f"✨ <b>{name}</b>, {age}, {city}\n"
        if match_with_me:
             kb_markup = get_profile_kb(quiet, uid)
        else:
             kb_markup = get_rating_kb(uid, voice_id)

    # Убрали упоминание чая из caption
    caption = f"{header}\n📝 {bio}"
    if qotd_ans:
        caption += f"\n\n💬 <b>На вопрос «{qotd_text}»:</b>\n{qotd_ans}"

    try:
        if c_type == 'video_note':
            await bot.send_video_note(chat_id, media_files[0])
            await bot.send_message(chat_id, caption, reply_markup=kb_markup, parse_mode="HTML")
        elif c_type == 'photo':
            if len(media_files) == 1:
                await bot.send_photo(chat_id, media_files[0], caption=caption, reply_markup=kb_markup, parse_mode="HTML")
            else:
                mg = [InputMediaPhoto(media=f) for f in media_files]
                await bot.send_media_group(chat_id, media=mg)
                await bot.send_message(chat_id, caption, reply_markup=kb_markup, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Error sending profile {uid}: {e}")
        await bot.send_message(chat_id, f"[Ошибка медиа]\n{caption}", reply_markup=kb_markup, parse_mode="HTML")

# --- СОСТОЯНИЯ ---
class Reg(StatesGroup):
    name = State()
    age = State()
    gender = State()
    interested_in = State()
    city = State()
    # Tea removed
    bio = State()
    media = State()

class AdminStates(StatesGroup):
    broadcast_text = State()
    qotd_text = State()

class EditProfile(StatesGroup):
    waiting_for_input = State()

class Mood(StatesGroup):
    status = State()

class AdminContact(StatesGroup):
    message = State()

class SearchMode(StatesGroup):
    random = State()   
    admirers = State()

class BlindDate(StatesGroup):
    searching = State()
    in_chat = State()
    deciding = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🌸 Искать пару"), KeyboardButton(text="👤 Моя анкета")],
        [KeyboardButton(text="🎭 Свидание вслепую")],
        [KeyboardButton(text="💘 Кто меня лайкнул"), KeyboardButton(text="💞 Взаимные")],
        [KeyboardButton(text="📓 Дневник"), KeyboardButton(text="📞 Админ")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_gender_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Парень 🧔"), KeyboardButton(text="Девушка 👩")]], resize_keyboard=True, one_time_keyboard=True)

def get_interest_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Парней 🧔"), KeyboardButton(text="Девушек 👩")], [KeyboardButton(text="Всех 🌈")]], resize_keyboard=True, one_time_keyboard=True)

def get_rating_kb(target_id, voice_id=None):
    # ЗАМЕНА: Чай на сердечко
    row1 = [InlineKeyboardButton(text="👎", callback_data="skip"), 
            InlineKeyboardButton(text="❤️", callback_data=f"vote_{target_id}_like"), 
            InlineKeyboardButton(text="🔥", callback_data=f"vote_{target_id}_love")]
    
    rows = [row1]
    
    if voice_id:
        rows.append([InlineKeyboardButton(text="🗣 Послушать голос", callback_data=f"play_voice_{target_id}")])
        
    rows.append([InlineKeyboardButton(text="💌 Пожаловаться", callback_data=f"report_{target_id}")])
    return InlineKeyboardMarkup(inline_keyboard=rows)

def get_profile_kb(quiet_mode, user_id=None):
    icon = "🔕" if quiet_mode else "🔔"
    # УБРАНА кнопка редактирования чая
    kb = [
        [InlineKeyboardButton(text=f"{icon} Уведомления", callback_data="toggle_quiet"),
         InlineKeyboardButton(text="📹 Фильтр видео", callback_data="toggle_video_filter")],
        [InlineKeyboardButton(text="📝 Текст", callback_data="edit_text"),
         InlineKeyboardButton(text="📸 Фото/Видео", callback_data="edit_media")],
        [InlineKeyboardButton(text="🗣 Голос", callback_data="edit_voice"),
         InlineKeyboardButton(text="💬 Ответ на вопрос дня", callback_data="edit_qotd")],
        [InlineKeyboardButton(text="🔄 Заполнить заново", callback_data="re_register")]
    ]
    return InlineKeyboardMarkup(inline_keyboard=kb)

def get_admin_panel_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats"),
         InlineKeyboardButton(text="📢 Рассылка", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⚠️ Жалобы (NEW)", callback_data="admin_reports"),
         InlineKeyboardButton(text="🧊 Сменить вопрос дня", callback_data="admin_set_qotd")]
    ])

def get_admin_action_kb(user_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚫 БАН", callback_data=f"ban_{user_id}"),
         InlineKeyboardButton(text="✅ Простить", callback_data=f"forgive_{user_id}")]
    ])

def get_blind_date_kb():
    return ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="❌ Прервать свидание")]
    ], resize_keyboard=True)

def get_reveal_kb():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❤️ Показать себя", callback_data="bd_reveal"),
         InlineKeyboardButton(text="🏃‍♂️ Уйти", callback_data="bd_leave")]
    ])

# ==========================================
#               РЕГИСТРАЦИЯ
# ==========================================
@dp.message(Command("start"), StateFilter("*"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET last_active = ? WHERE id = ?", (datetime.now(), message.from_user.id))
        await db.commit()
        
        async with db.execute("SELECT id FROM users WHERE id = ?", (message.from_user.id,)) as c:
            if await c.fetchone():
                await message.answer("С возвращением! 🌿", reply_markup=get_main_menu())
                return

    await message.answer("Здравствуй! ✨\nДавай создадим твой уютный профиль.\nКак тебя зовут?")
    await state.set_state(Reg.name)

@dp.message(Reg.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Сколько тебе лет?")
    await state.set_state(Reg.age)

@dp.message(Reg.age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit(): return await message.answer("Только цифры.")
    await state.update_data(age=int(message.text))
    await message.answer("Укажи свой пол:", reply_markup=get_gender_kb())
    await state.set_state(Reg.gender)

@dp.message(Reg.gender)
async def process_gender(message: types.Message, state: FSMContext):
    if "Парень" not in message.text and "Девушка" not in message.text:
         return await message.answer("Используй кнопки.")
    code = "M" if "Парень" in message.text else "F"
    await state.update_data(gender=code)
    await message.answer("Кого ищем?", reply_markup=get_interest_kb())
    await state.set_state(Reg.interested_in)

@dp.message(Reg.interested_in)
async def process_inter(message: types.Message, state: FSMContext):
    code = "M" if "Парней" in message.text else ("F" if "Девушек" in message.text else "ALL")
    await state.update_data(interested_in=code)
    await message.answer("Твой город? (будет просто отображаться в анкете)", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Reg.city)

@dp.message(Reg.city)
async def process_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    # ПРОПУСК ЧАЯ: сразу идем к BIO
    qotd = await get_qotd()
    await message.answer(f"Пару слов о себе. 📝\n\nКстати, можешь сразу ответить на вопрос дня: <i>{qotd}</i>", parse_mode="HTML")
    await state.set_state(Reg.bio)

@dp.message(Reg.bio)
async def process_bio(message: types.Message, state: FSMContext):
    await state.update_data(bio=message.text)
    await message.answer("Пришли фото (до 3х), **видео-кружочек** или **голосовое приветствие**! 📸🎙\n(Голос повышает доверие!)", parse_mode="Markdown")
    await state.set_state(Reg.media)

@dp.message(Reg.media)
async def process_media(message: types.Message, state: FSMContext):
    data = await state.get_data()
    
    if message.voice:
        if 'temp_voice' not in data:
            await state.update_data(temp_voice=message.voice.file_id)
            await message.answer("Голос записан! 🗣 Теперь пришли фото или видео-кружочек, чтобы тебя увидели.")
            return
        else:
             await message.answer("Голос уже есть. Жду фото.")
             return

    voice = data.get('temp_voice', None)

    if message.video_note:
        await finish_reg(message, state, [message.video_note.file_id], 'video_note', voice)
    elif message.photo:
        await finish_reg(message, state, [message.photo[-1].file_id], 'photo', voice)
    else:
        await message.answer("Жду фото или кружочек.")

async def finish_reg(message, state, content, c_type, voice_id=None):
    data = await state.get_data()
    is_verified = 1 if message.from_user.id == ADMIN_ID else 0
    
    # tea_pref заполняем пустой строкой
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (id, username, name, age, gender, interested_in, city, bio, tea_pref, content_ids, content_type, is_verified, last_active, reg_date, voice_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message.from_user.id, message.from_user.username, data['name'], data['age'], 
              data['gender'], data['interested_in'], data['city'], data['bio'], "", 
              json.dumps(content), c_type, is_verified, datetime.now(), datetime.now(), voice_id))
        await db.commit()
    
    await state.clear()
    await message.answer("Анкета отправлена! ⏳", reply_markup=get_main_menu())
    
    if not is_verified:
        await bot.send_message(ADMIN_ID, f"🆕 Новая анкета: {data['name']}, {data['city']}", reply_markup=get_admin_action_kb(message.from_user.id))

# ==========================================
#               АДМИН ПАНЕЛЬ
# ==========================================
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    await message.answer("👮‍♂️ <b>Панель управления уютом</b>", reply_markup=get_admin_panel_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_stats")
async def show_stats(cb: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c: total = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE last_active > datetime('now', '-1 day')") as c: dau = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE gender='M'") as c: m = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE gender='F'") as c: f = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM votes WHERE timestamp > datetime('now', '-1 day') AND reaction IN ('like','love')") as c: likes = (await c.fetchone())[0]
        
    txt = (f"📊 <b>Статистика:</b>\n\n"
           f"👥 Всего: {total}\n"
           f"🔥 Актив (24ч): {dau}\n"
           f"⚖️ М/Ж: {m} / {f}\n"
           f"❤️ Лайков за сутки: {likes}")
    await cb.message.edit_text(txt, reply_markup=get_admin_panel_kb(), parse_mode="HTML")

@dp.callback_query(F.data == "admin_reports")
async def show_reports(cb: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        sql = "SELECT * FROM users WHERE report_count > 0 AND is_banned = 0 ORDER BY report_count DESC LIMIT 1"
        async with db.execute(sql) as c:
            user = await c.fetchone()
    
    if not user:
        await cb.answer("Жалоб нет! Чистота и порядок. ✨")
        return

    await cb.message.answer(f"⚠️ <b>Жалоба (всего: {user[16]})</b>", parse_mode="HTML")
    await send_user_profile(cb.message.chat.id, user, admin_view=True)

@dp.callback_query(F.data.startswith("ban_"))
async def ban_user(cb: types.CallbackQuery):
    uid = int(cb.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_banned = 1 WHERE id = ?", (uid,))
        await db.commit()
    await cb.answer("Пользователь забанен.")
    await cb.message.delete()

@dp.callback_query(F.data.startswith("forgive_"))
async def forgive_user(cb: types.CallbackQuery):
    uid = int(cb.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET report_count = 0 WHERE id = ?", (uid,))
        await db.commit()
    await cb.answer("Жалобы обнулены.")
    await cb.message.delete()

@dp.callback_query(F.data == "admin_set_qotd")
async def start_set_qotd(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Введите новый Вопрос Дня:")
    await state.set_state(AdminStates.qotd_text)

@dp.message(AdminStates.qotd_text)
async def save_qotd(message: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO config (key, value) VALUES ('qotd', ?)", (message.text,))
        await db.commit()
    await message.answer("Вопрос дня обновлен! 🧊")
    await state.clear()

@dp.callback_query(F.data == "admin_broadcast")
async def start_broadcast(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Пришли текст рассылки (можно с фото):")
    await state.set_state(AdminStates.broadcast_text)

@dp.message(AdminStates.broadcast_text)
async def send_broadcast(message: types.Message, state: FSMContext):
    msg_text = message.text or message.caption
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id FROM users") as cursor:
            users = await cursor.fetchall()
    
    count = 0
    await message.answer(f"Начинаю рассылку на {len(users)} человек...")
    
    for (uid,) in users:
        try:
            if message.photo:
                await bot.send_photo(uid, message.photo[-1].file_id, caption=msg_text)
            else:
                await bot.send_message(uid, f"🔔 <b>Новости:</b>\n{msg_text}", parse_mode="HTML")
            count += 1
            await asyncio.sleep(0.05)
        except: pass
            
    await message.answer(f"Рассылка завершена. Дошло: {count}")
    await state.clear()

# ==========================================
#               КНОПКИ МЕНЮ
# ==========================================

@dp.message(F.text == "📞 Админ", StateFilter("*"))
async def contact_admin_start(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Напиши сообщение админу (или предложение). 🖊\n(/cancel для отмены)")
    await state.set_state(AdminContact.message)

@dp.message(AdminContact.message)
async def contact_admin_send(message: types.Message, state: FSMContext):
    if message.text == "/cancel":
        await state.clear()
        await message.answer("Отменено.", reply_markup=get_main_menu())
        return

    text_to_admin = f"📩 **Сообщение от пользователя**\nОт: {message.from_user.full_name} (ID: `{message.from_user.id}`)\n\n{message.text}"
    try:
        await bot.send_message(ADMIN_ID, text_to_admin, parse_mode="Markdown")
        await message.answer("Сообщение отправлено! Спасибо. 📨", reply_markup=get_main_menu())
    except Exception as e:
        await message.answer("Ошибка отправки. Попробуй позже.")
        logging.error(e)
    
    await state.clear()

@dp.message(F.text == "📓 Дневник", StateFilter("*"))
async def mood_diary(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Какая погода у тебя в душе? 🌦")
    await state.set_state(Mood.status)

@dp.message(Mood.status)
async def process_mood(message: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET mood_today = ? WHERE id = ?", (message.text, message.from_user.id))
        await db.commit()
    await message.answer("Записал в дневник. 🫂", reply_markup=get_main_menu())
    await state.clear()

@dp.message(F.text == "💞 Взаимные", StateFilter("*"))
async def show_mutual_likes(message: types.Message, state: FSMContext):
    await state.clear()
    my_id = message.chat.id
    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT u.name, u.username, u.id 
            FROM users u
            JOIN votes v1 ON u.id = v1.to_id 
            JOIN votes v2 ON u.id = v2.from_id
            WHERE v1.from_id = ? AND v1.reaction IN ('like', 'love')
            AND v2.to_id = ? AND v2.reaction IN ('like', 'love')
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            matches = await cursor.fetchall()

    if not matches:
        await message.answer("Пока нет взаимных симпатий. Продолжай искать! 🌸")
        return

    text = "<b>💞 Твои взаимные симпатии:</b>\n\n"
    for name, username, uid in matches:
        link = get_profile_link(uid, username, name)
        text += f"• {link}\n"
    await message.answer(text, parse_mode="HTML", disable_web_page_preview=True)

@dp.message(F.text == "💘 Кто меня лайкнул", StateFilter("*"))
async def show_who_liked_me(message: types.Message, state: FSMContext):
    await state.clear()
    await state.set_state(SearchMode.admirers)
    my_id = message.chat.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        # Ищем тех, кто меня лайкнул, но кому я еще ничего не ответил
        sql = """
            SELECT u.*
            FROM users u
            JOIN votes v ON u.id = v.from_id
            WHERE v.to_id = ? AND v.reaction IN ('like', 'love')
            AND u.id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)
            LIMIT 1
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            user = await cursor.fetchone()

    if not user:
        await message.answer("Новых лайков пока нет. Перехожу к общему поиску... 🌸")
        await search_profiles(message, state)
        return

    await message.answer("💘 <b>Ты понравился этому человеку!</b>", parse_mode="HTML")
    await send_user_profile(my_id, user, is_match=False, match_with_me=False)

# ==========================================
#         ФУНКЦИОНАЛ: СВИДАНИЕ ВСЛЕПУЮ
# ==========================================
@dp.message(F.text == "🎭 Свидание вслепую")
async def start_blind_date(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT gender, interested_in FROM users WHERE id=?", (uid,)) as c:
            user_info = await c.fetchone()
    
    if not user_info:
        return await message.answer("Сначала заполни анкету через /start!")

    my_gender, my_interest = user_info[0], user_info[1]

    # Поиск пары
    partner_id = None
    for q_uid, q_data in list(BLIND_DATE_QUEUE.items()):
        if q_uid == uid: continue
        
        partner_ok = (q_data['interest'] == 'ALL' or q_data['interest'] == my_gender)
        me_ok = (my_interest == 'ALL' or my_interest == q_data['gender'])
        
        if partner_ok and me_ok:
            partner_id = q_uid
            break
            
    if partner_id:
        del BLIND_DATE_QUEUE[partner_id]
        await start_blind_chat(uid, partner_id, state)
    else:
        BLIND_DATE_QUEUE[uid] = {'gender': my_gender, 'interest': my_interest}
        await message.answer("🎭 <b>Поиск тайного собеседника...</b>\nОжидай, я пришлю уведомление, когда кто-то найдется.\n\nПока можешь пользоваться ботом, но если начнешь обычный поиск, выйди из очереди.", parse_mode="HTML")
        await state.set_state(BlindDate.searching)

async def start_blind_chat(user1_id, user2_id, state1):
    state2 = dp.fsm.resolve_context(bot=bot, chat_id=user2_id, user_id=user2_id)
    
    await state1.set_state(BlindDate.in_chat)
    await state1.update_data(partner_id=user2_id)
    
    await state2.set_state(BlindDate.in_chat)
    await state2.update_data(partner_id=user1_id)
    
    msg = ("🎭 <b>Собеседник найден!</b>\n\n"
           "У вас есть 15 минут. Имен и фото не видно.\n"
           "В конце вы сможете раскрыть личности, если оба захотите.\n"
           "Нажмите «❌ Прервать свидание», чтобы выйти раньше.")
    
    kb = get_blind_date_kb()
    await bot.send_message(user1_id, msg, reply_markup=kb, parse_mode="HTML")
    await bot.send_message(user2_id, msg, reply_markup=kb, parse_mode="HTML")
    
    asyncio.create_task(blind_date_timer(user1_id, user2_id))

async def blind_date_timer(u1, u2):
    await asyncio.sleep(15 * 60)
    try:
        await stop_blind_chat_logic(u1, u2, timeout=True)
    except: pass

@dp.message(BlindDate.in_chat, F.text == "❌ Прервать свидание")
async def stop_blind_chat_manual(message: types.Message, state: FSMContext):
    data = await state.get_data()
    partner_id = data.get('partner_id')
    if partner_id:
        await stop_blind_chat_logic(message.from_user.id, partner_id)

async def stop_blind_chat_logic(u1, u2, timeout=False):
    s1 = dp.fsm.resolve_context(bot=bot, chat_id=u1, user_id=u1)
    s2 = dp.fsm.resolve_context(bot=bot, chat_id=u2, user_id=u2)
    
    reason = "Время вышло!" if timeout else "Собеседник покинул чат."
    
    await s1.set_state(BlindDate.deciding)
    await s2.set_state(BlindDate.deciding)
    
    await s1.update_data(partner_id=u2, revealed=False)
    await s2.update_data(partner_id=u1, revealed=False)
    
    text = f"🏁 <b>Свидание окончено.</b> {reason}\nХотите показать свою анкету?"
    
    try: await bot.send_message(u1, text, reply_markup=get_reveal_kb(), parse_mode="HTML") 
    except: pass
    try: await bot.send_message(u2, text, reply_markup=get_reveal_kb(), parse_mode="HTML")
    except: pass

@dp.message(BlindDate.in_chat)
async def relay_blind_message(message: types.Message, state: FSMContext):
    data = await state.get_data()
    partner_id = data.get('partner_id')
    
    if not partner_id:
        await message.answer("Ошибка связи. Выхожу.")
        await state.clear()
        return

    try:
        if message.text:
            await bot.send_message(partner_id, message.text)
        elif message.photo:
            await bot.send_photo(partner_id, message.photo[-1].file_id, caption=message.caption)
        elif message.voice:
            await bot.send_voice(partner_id, message.voice.file_id)
        elif message.video_note:
            await bot.send_video_note(partner_id, message.video_note.file_id)
        elif message.sticker:
            await bot.send_sticker(partner_id, message.sticker.file_id)
        else:
            await message.answer("Этот тип сообщений не поддерживается в слепом чате.")
    except Exception as e:
        await message.answer("Собеседник отключился.")
        await stop_blind_chat_logic(message.from_user.id, partner_id)

@dp.callback_query(F.data == "bd_leave")
async def blind_date_leave(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.edit_text("Вы ушли в туман... 🌫", reply_markup=None)
    await cb.message.answer("Главное меню", reply_markup=get_main_menu())

@dp.callback_query(F.data == "bd_reveal")
async def blind_date_reveal(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Вы согласились показать анкету! Ждем решения партнера... ⏳", reply_markup=None)
    
    data = await state.get_data()
    partner_id = data.get('partner_id')
    
    await state.update_data(revealed=True)
    
    partner_state = dp.fsm.resolve_context(bot=bot, chat_id=partner_id, user_id=partner_id)
    p_data = await partner_state.get_data()
    
    if p_data.get('revealed'):
        await bot.send_message(cb.from_user.id, "💖 <b>Оба согласны! Вот анкета партнера:</b>", parse_mode="HTML")
        await bot.send_message(partner_id, "💖 <b>Оба согласны! Вот анкета партнера:</b>", parse_mode="HTML")
        
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT * FROM users WHERE id=?", (partner_id,)) as c: p_user = await c.fetchone()
            async with db.execute("SELECT * FROM users WHERE id=?", (cb.from_user.id,)) as c: my_user = await c.fetchone()
            
        await send_user_profile(cb.from_user.id, p_user, is_match=True)
        await send_user_profile(partner_id, my_user, is_match=True)
        
        await state.clear()
        await partner_state.clear()

# ==========================================
#               ПОИСК И АНКЕТЫ
# ==========================================
@dp.message(F.text == "👤 Моя анкета")
async def my_profile(message: types.Message, state: FSMContext):
    if message.from_user.id in BLIND_DATE_QUEUE:
        del BLIND_DATE_QUEUE[message.from_user.id]

    await state.clear()
    uid = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE id = ?", (uid,)) as c:
            user = await c.fetchone()
    
    if not user: return await message.answer("Сначала /start")
    
    v_filter = "ВКЛ" if user[12] == 1 else "ВЫКЛ"
    
    await send_user_profile(uid, user, match_with_me=True)
    await message.answer(f"📹 Фильтр 'Только видео': <b>{v_filter}</b>", parse_mode="HTML")

@dp.callback_query(F.data == "toggle_video_filter")
async def toggle_video(cb: types.CallbackQuery):
    uid = cb.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT search_video_only FROM users WHERE id=?", (uid,)) as c:
            curr = (await c.fetchone())[0]
        new_val = 0 if curr == 1 else 1
        await db.execute("UPDATE users SET search_video_only = ? WHERE id = ?", (new_val, uid))
        await db.commit()
    
    status = "включен (ищем только кружочки)" if new_val else "выключен"
    await cb.answer(f"Фильтр видео {status}")
    await my_profile(cb.message, None) 

@dp.message(F.text == "🌸 Искать пару")
async def search_profiles(message: types.Message, state: FSMContext):
    if message.from_user.id in BLIND_DATE_QUEUE:
        del BLIND_DATE_QUEUE[message.from_user.id]

    uid = message.from_user.id
    
    current_state = await state.get_state()
    if current_state != SearchMode.admirers:
         await state.set_state(SearchMode.random)
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET last_active = ? WHERE id = ?", (datetime.now(), uid))
        await db.commit()
        
        async with db.execute("SELECT gender, interested_in, search_video_only FROM users WHERE id=?", (uid,)) as c:
            me = await c.fetchone()
            if not me: return

    my_gender, interest, video_only = me[0], me[1], me[2]
    
    filters = ["id != ?", "is_verified = 1", "is_banned = 0"]
    params = [uid]

    if interest != "ALL":
        filters.append("gender = ?")
        params.append(interest)
    
    if video_only:
        filters.append("content_type = 'video_note'")
        
    filters.append("id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)")
    params.append(uid)

    where_clause = " AND ".join(filters)
    sql = f"SELECT * FROM users WHERE {where_clause} ORDER BY RANDOM() LIMIT 1"

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute(sql, tuple(params)) as c:
            user = await c.fetchone()
    
    if not user:
        await message.answer("Анкеты по твоим параметрам закончились. 😔\nПопробуй отключить видео-фильтр или зайди позже.")
        return

    await send_user_profile(uid, user)

@dp.callback_query(F.data.startswith("play_voice_"))
async def play_voice_handler(cb: types.CallbackQuery):
    target_id = int(cb.data.split("_")[2])
    
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT voice_id FROM users WHERE id=?", (target_id,)) as c:
            res = await c.fetchone()
            
    if res and res[0]:
        await cb.message.answer_voice(res[0], caption="🎙 Голос пользователя")
        await cb.answer()
    else:
        await cb.answer("Голос не найден или удален.", show_alert=True)

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(cb: types.CallbackQuery, state: FSMContext):
    _, target_id, reaction = cb.data.split("_")
    target_id = int(target_id)
    uid = cb.from_user.id
    
    await cb.message.delete()
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO votes (from_id, to_id, reaction, timestamp) VALUES (?, ?, ?, ?)", 
                         (uid, target_id, reaction, datetime.now()))
        await db.commit()
        
        if reaction in ['like', 'love']:
            async with db.execute("SELECT reaction FROM votes WHERE from_id=? AND to_id=?", (target_id, uid)) as c:
                match = await c.fetchone()
            
            if match and match[0] in ['like', 'love']:
                # МЭТЧ
                async with db.execute("SELECT * FROM users WHERE id=?", (target_id,)) as c: t_data = await c.fetchone()
                async with db.execute("SELECT * FROM users WHERE id=?", (uid,)) as c: m_data = await c.fetchone()
                
                await send_user_profile(uid, t_data, is_match=True)
                
                is_quiet = False
                try:
                    if t_data[17] == 1: is_quiet = True
                except: pass
                
                if not is_quiet and not is_quiet_hours():
                    await send_user_profile(target_id, m_data, is_match=True)
            
            elif reaction == 'love':
                if not is_quiet_hours():
                     try: await bot.send_message(target_id, "Кто-то отправил тебе 💘!") 
                     except: pass
    
    current_state = await state.get_state()
    if current_state == SearchMode.admirers:
        await show_who_liked_me(cb.message, state)
    else:
        await search_profiles(cb.message, state)

@dp.callback_query(F.data == "skip")
async def skip_prof(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.delete()
    
    current_state = await state.get_state()
    if current_state == SearchMode.admirers:
        await show_who_liked_me(cb.message, state)
    else:
        await search_profiles(cb.message, state)

@dp.callback_query(F.data == "toggle_quiet")
async def toggle_quiet(cb: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT quiet_mode FROM users WHERE id=?", (cb.from_user.id,)) as c:
            curr = (await c.fetchone())[0]
        new_val = 0 if curr == 1 else 1
        await db.execute("UPDATE users SET quiet_mode = ? WHERE id = ?", (new_val, cb.from_user.id))
        await db.commit()
    await cb.message.edit_reply_markup(reply_markup=get_profile_kb(new_val))

@dp.callback_query(F.data == "re_register")
async def re_register(cb: types.CallbackQuery, state: FSMContext):
    await state.clear()
    await cb.message.answer("Давай создадим новую анкету. Как тебя зовут?")
    await state.set_state(Reg.name)

# --- РЕДАКТИРОВАНИЕ ПРОФИЛЯ ---
@dp.callback_query(F.data == "edit_qotd")
async def edit_qotd_start(cb: types.CallbackQuery, state: FSMContext):
    q = await get_qotd()
    await cb.message.answer(f"Вопрос дня: {q}\n\nНапиши свой ответ:")
    await state.set_state(EditProfile.waiting_for_input)
    await state.update_data(mode="qotd")

@dp.callback_query(F.data == "edit_text")
async def edit_text_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Напиши новый текст 'О себе':")
    await state.set_state(EditProfile.waiting_for_input)
    await state.update_data(mode="text")

# Редактирование чая удалено

@dp.callback_query(F.data == "edit_media")
async def edit_media_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Пришли новое фото или видео-кружочек:")
    await state.set_state(EditProfile.waiting_for_input)
    await state.update_data(mode="media")

@dp.callback_query(F.data == "edit_voice")
async def edit_voice_start(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Запиши голосовое приветствие (до 20 сек):")
    await state.set_state(EditProfile.waiting_for_input)
    await state.update_data(mode="voice")

@dp.message(EditProfile.waiting_for_input)
async def save_profile_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("mode")
    uid = message.from_user.id
    
    async with aiosqlite.connect(DB_NAME) as db:
        if mode == "qotd":
            await db.execute("UPDATE users SET qotd_answer = ? WHERE id = ?", (message.text, uid))
            await message.answer("Ответ сохранен! 👌")
        elif mode == "text":
            await db.execute("UPDATE users SET bio = ? WHERE id = ?", (message.text, uid))
            await message.answer("Био обновлено!")
        elif mode == "media":
            if message.video_note:
                 c = json.dumps([message.video_note.file_id])
                 t = "video_note"
            elif message.photo:
                 c = json.dumps([message.photo[-1].file_id])
                 t = "photo"
            else: return await message.answer("Пришли медиа!")
            
            await db.execute("UPDATE users SET content_ids = ?, content_type = ? WHERE id = ?", (c, t, uid))
            await message.answer("Медиа обновлено!")
        elif mode == "voice":
            if message.voice:
                await db.execute("UPDATE users SET voice_id = ? WHERE id = ?", (message.voice.file_id, uid))
                await message.answer("Голос обновлен! 🎙")
            else:
                return await message.answer("Это не голосовое сообщение.")

        await db.commit()
    
    await state.clear()
    await my_profile(message, state)

# --- MAIN ---
async def main():
    await init_db()
    print("Bot is running WITHOUT TEA 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
