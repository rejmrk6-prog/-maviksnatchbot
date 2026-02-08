import asyncio
import logging
import json
import aiosqlite
from datetime import datetime, time

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
TOKEN = "ТВОЙ_ТОКЕН" 
ADMIN_ID = 7467909699
DB_NAME = "cozy_dating.db"

# Инициализация
bot = Bot(token=TOKEN)
dp = Dispatcher()
logging.basicConfig(level=logging.INFO)

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        # Основная таблица пользователей
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
                qotd_answer TEXT,       -- Ответ на вопрос дня
                content_ids TEXT,
                content_type TEXT,
                tea_pref TEXT,
                search_video_only INTEGER DEFAULT 0, -- Фильтр только видео
                is_active INTEGER DEFAULT 1,
                is_banned INTEGER DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                report_count INTEGER DEFAULT 0,
                quiet_mode INTEGER DEFAULT 0,
                last_active DATETIME,
                reg_date DATETIME
            )
        """)
        # Таблица лайков/дизлайков
        await db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                from_id INTEGER,
                to_id INTEGER,
                reaction TEXT,
                timestamp DATETIME,
                UNIQUE(from_id, to_id)
            )
        """)
        # Таблица настроек (хранит Вопрос Дня)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)
        # Инициализация вопроса дня по умолчанию
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

async def check_tea_compatibility(tea1, tea2):
    """Простая проверка совместимости по ключевым словам чая"""
    if not tea1 or not tea2: return False
    keywords = ["зеленый", "черный", "пуэр", "улун", "каркаде", "травяной", "мята", "чабрец", "кофе", "матча"]
    t1 = tea1.lower()
    t2 = tea2.lower()
    for k in keywords:
        if k in t1 and k in t2:
            return k # Возвращаем совпавший вкус
    return None

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

    # Распаковка (с учетом новых полей)
    uid = user_data[0]
    username = user_data[1]
    name = user_data[2]
    age = user_data[3]
    city = user_data[6]
    bio = user_data[7]
    qotd_ans = user_data[8]
    content_ids_raw = user_data[9]
    c_type = user_data[10]
    tea_pref = user_data[11]
    # user_data[12] = search_video_only
    quiet = user_data[17] if len(user_data) > 17 else 0
    
    # Декодинг медиа
    try:
        media_files = json.loads(content_ids_raw)
        if not isinstance(media_files, list): media_files = [content_ids_raw]
    except: media_files = []

    # Тексты
    qotd_text = await get_qotd()
    
    # Проверка совместимости по чаю (если смотрим чужой профиль)
    tea_match_text = ""
    if not match_with_me and not is_match and not admin_view:
        async with aiosqlite.connect(DB_NAME) as db:
            async with db.execute("SELECT tea_pref FROM users WHERE id=?", (chat_id,)) as c:
                my_tea = (await c.fetchone())
                if my_tea:
                    match_flavor = await check_tea_compatibility(my_tea[0], tea_pref)
                    if match_flavor:
                        tea_match_text = f"\n🍃 <b>Вы оба любите {match_flavor}! Отличный повод обсудить это.</b>"

    if is_match:
        header = f"💖 <b>ЭТО ВЗАИМНО!</b>\nКонтакт: {get_profile_link(uid, username, name)}\n"
        # Safe Start Suggestion
        header += f"\n🎲 <b>Тема для старта:</b>\n<i>«{qotd_text}»</i>\nСпроси, что {name} думает об этом!"
        kb = None
    elif admin_view:
        header = f"🕵️ <b>Админ-просмотр:</b> {name}, {age}\nID: `{uid}`"
        kb = get_admin_action_kb(uid)
    else:
        header = f"✨ <b>{name}</b>, {age}, {city}\n"
        if match_with_me:
             kb = get_profile_kb(quiet)
        else:
             kb = get_rating_kb(uid)

    caption = f"{header}\n☕ {tea_pref}{tea_match_text}\n📝 {bio}"
    if qotd_ans:
        caption += f"\n\n💬 <b>На вопрос «{qotd_text}»:</b>\n{qotd_ans}"

    try:
        if c_type == 'video_note':
            await bot.send_video_note(chat_id, media_files[0])
            await bot.send_message(chat_id, caption, reply_markup=kb, parse_mode="HTML")
        elif c_type == 'photo':
            if len(media_files) == 1:
                await bot.send_photo(chat_id, media_files[0], caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                mg = [InputMediaPhoto(media=f) for f in media_files]
                await bot.send_media_group(chat_id, media=mg)
                await bot.send_message(chat_id, caption, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Error sending profile {uid}: {e}")
        await bot.send_message(chat_id, f"[Ошибка медиа]\n{caption}", reply_markup=kb, parse_mode="HTML")

# --- СОСТОЯНИЯ ---
class Reg(StatesGroup):
    name = State()
    age = State()
    gender = State()
    interested_in = State()
    city = State()
    tea = State()
    bio = State()
    media = State()

class AdminStates(StatesGroup):
    broadcast_text = State()
    qotd_text = State()

class EditProfile(StatesGroup):
    waiting_for_input = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🌸 Искать пару"), KeyboardButton(text="👤 Моя анкета")],
        [KeyboardButton(text="💘 Кто меня лайкнул"), KeyboardButton(text="💞 Взаимные")],
        [KeyboardButton(text="📓 Дневник"), KeyboardButton(text="📞 Админ")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_gender_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Парень 🧔"), KeyboardButton(text="Девушка 👩")]], resize_keyboard=True, one_time_keyboard=True)

def get_interest_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="Парней 🧔"), KeyboardButton(text="Девушек 👩")], [KeyboardButton(text="Всех 🌈")]], resize_keyboard=True, one_time_keyboard=True)

def get_rating_kb(target_id):
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👎", callback_data="skip"), 
         InlineKeyboardButton(text="☕️", callback_data=f"vote_{target_id}_like"), 
         InlineKeyboardButton(text="💘", callback_data=f"vote_{target_id}_love")],
        [InlineKeyboardButton(text="💌 Пожаловаться", callback_data=f"report_{target_id}")]
    ])

def get_profile_kb(quiet_mode):
    # Добавлена кнопка редактирования "Вопроса дня" и фильтров
    icon = "🔕" if quiet_mode else "🔔"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{icon} Уведомления", callback_data="toggle_quiet"),
         InlineKeyboardButton(text="📹 Фильтр видео", callback_data="toggle_video_filter")],
        [InlineKeyboardButton(text="📝 Текст", callback_data="edit_text"),
         InlineKeyboardButton(text="📸 Фото/Видео", callback_data="edit_media")],
        [InlineKeyboardButton(text="☕️ Чай", callback_data="edit_tea"),
         InlineKeyboardButton(text="💬 Ответ на вопрос дня", callback_data="edit_qotd")],
        [InlineKeyboardButton(text="🔄 Заполнить заново", callback_data="re_register")]
    ])

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

# ==========================================
#               РЕГИСТРАЦИЯ
# ==========================================
@dp.message(Command("start"), StateFilter("*"))
async def cmd_start(message: types.Message, state: FSMContext):
    await state.clear()
    # Обновляем last_active
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET last_active = ? WHERE id = ?", (datetime.now(), message.from_user.id))
        await db.commit()
        
        async with db.execute("SELECT id FROM users WHERE id = ?", (message.from_user.id,)) as c:
            if await c.fetchone():
                await message.answer("С возвращением! 🌿", reply_markup=get_main_menu())
                return

    await message.answer("Здравствуй! ✨\nДавай создадим твой уютный профиль.\nКак тебя зовут?")
    await state.set_state(Reg.name)

# ... (Процесс регистрации имени и возраста стандартный) ...
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
    code = "M" if "Парень" in message.text else "F"
    await state.update_data(gender=code)
    await message.answer("Кого ищем?", reply_markup=get_interest_kb())
    await state.set_state(Reg.interested_in)

@dp.message(Reg.interested_in)
async def process_inter(message: types.Message, state: FSMContext):
    code = "M" if "Парней" in message.text else ("F" if "Девушек" in message.text else "ALL")
    await state.update_data(interested_in=code)
    await message.answer("Твой город?", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Reg.city)

@dp.message(Reg.city)
async def process_city(message: types.Message, state: FSMContext):
    await state.update_data(city=message.text)
    await message.answer("Любимый чай или что согревает душу? ☕️")
    await state.set_state(Reg.tea)

@dp.message(Reg.tea)
async def process_tea(message: types.Message, state: FSMContext):
    await state.update_data(tea=message.text)
    qotd = await get_qotd()
    await message.answer(f"Пару слов о себе. 📝\n\nКстати, можешь сразу ответить на вопрос дня: <i>{qotd}</i>")
    await state.set_state(Reg.bio)

@dp.message(Reg.bio)
async def process_bio(message: types.Message, state: FSMContext):
    await state.update_data(bio=message.text)
    await message.answer("Пришли фото (до 3х) или **видео-кружочек** (лучше для поиска!). 📸")
    await state.set_state(Reg.media)

@dp.message(Reg.media)
async def process_media(message: types.Message, state: FSMContext):
    # (Упрощенная загрузка для краткости)
    if message.video_note:
        await finish_reg(message, state, [message.video_note.file_id], 'video_note')
    elif message.photo:
        await finish_reg(message, state, [message.photo[-1].file_id], 'photo')
    else:
        await message.answer("Жду фото или кружочек.")

async def finish_reg(message, state, content, c_type):
    data = await state.get_data()
    is_verified = 1 if message.from_user.id == ADMIN_ID else 0
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (id, username, name, age, gender, interested_in, city, bio, tea_pref, content_ids, content_type, is_verified, last_active, reg_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message.from_user.id, message.from_user.username, data['name'], data['age'], 
              data['gender'], data['interested_in'], data['city'], data['bio'], data['tea'], 
              json.dumps(content), c_type, is_verified, datetime.now(), datetime.now()))
        await db.commit()
    
    await state.clear()
    await message.answer("Анкета отправлена! ⏳", reply_markup=get_main_menu())
    
    # Уведомление админу
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
        # Active in last 24h
        async with db.execute("SELECT COUNT(*) FROM users WHERE last_active > datetime('now', '-1 day')") as c: dau = (await c.fetchone())[0]
        # Gender ratio
        async with db.execute("SELECT COUNT(*) FROM users WHERE gender='M'") as c: m = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM users WHERE gender='F'") as c: f = (await c.fetchone())[0]
        # Matches today
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
        # Берем юзера с наибольшим числом репортов
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
            await asyncio.sleep(0.05) # Лимит телеграма
        except:
            pass # Бот заблокирован пользователем
            
    await message.answer(f"Рассылка завершена. Дошло: {count}")
    await state.clear()

# ==========================================
#               ПОИСК И АНКЕТЫ
# ==========================================
@dp.message(F.text == "👤 Моя анкета")
async def my_profile(message: types.Message, state: FSMContext):
    await state.clear()
    uid = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE id = ?", (uid,)) as c:
            user = await c.fetchone()
    
    if not user: return await message.answer("Сначала /start")
    
    # Статус видео фильтра
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
    # Обновляем сообщение
    await my_profile(cb.message, None) 

@dp.message(F.text == "🌸 Искать пару")
async def search_profiles(message: types.Message, state: FSMContext):
    uid = message.from_user.id
    
    # Обновляем время активности
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET last_active = ? WHERE id = ?", (datetime.now(), uid))
        await db.commit()
        
        # Получаем параметры поиска
        async with db.execute("SELECT gender, interested_in, search_video_only FROM users WHERE id=?", (uid,)) as c:
            me = await c.fetchone()
            if not me: return

    my_gender, interest, video_only = me[0], me[1], me[2]
    
    # SQL Конструктор
    filters = ["id != ?", "is_verified = 1", "is_banned = 0"]
    params = [uid]

    if interest != "ALL":
        filters.append("gender = ?")
        params.append(interest)
    
    # Фильтр "Только видео"
    if video_only:
        filters.append("content_type = 'video_note'")
        
    # Исключаем тех, кого уже видели
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

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(cb: types.CallbackQuery):
    _, target_id, reaction = cb.data.split("_")
    target_id = int(target_id)
    uid = cb.from_user.id
    
    await cb.message.delete()
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR IGNORE INTO votes (from_id, to_id, reaction, timestamp) VALUES (?, ?, ?, ?)", 
                         (uid, target_id, reaction, datetime.now()))
        await db.commit()
        
        # Если лайк
        if reaction in ['like', 'love']:
            # Проверка взаимности
            async with db.execute("SELECT reaction FROM votes WHERE from_id=? AND to_id=?", (target_id, uid)) as c:
                match = await c.fetchone()
            
            if match and match[0] in ['like', 'love']:
                # МЭТЧ!
                async with db.execute("SELECT * FROM users WHERE id=?", (target_id,)) as c: t_data = await c.fetchone()
                async with db.execute("SELECT * FROM users WHERE id=?", (uid,)) as c: m_data = await c.fetchone()
                
                # Отправка мне
                await send_user_profile(uid, t_data, is_match=True)
                
                # Отправка ему (с учетом Ночного Режима)
                is_quiet = False
                try:
                    if t_data[17] == 1: is_quiet = True # Проверка quiet_mode юзера
                except: pass
                
                if not is_quiet and not is_quiet_hours():
                    await send_user_profile(target_id, m_data, is_match=True)
                elif is_quiet_hours():
                    # Можно сохранить в "отложенные", но пока просто не шлем уведомление, увидит в "Взаимные"
                    pass
            
            elif reaction == 'love':
                # Суперлайк уведомление (если не ночь)
                if not is_quiet_hours():
                     try: await bot.send_message(target_id, "Кто-то отправил тебе 💘!") 
                     except: pass

    # Следующая анкета
    await search_profiles(cb.message, None)

@dp.callback_query(F.data == "skip")
async def skip_prof(cb: types.CallbackQuery):
    await cb.message.delete()
    # Можно записывать дизлайк, чтобы не показывать снова
    async with aiosqlite.connect(DB_NAME) as db:
        # Получаем ID из коллбека предыдущей кнопки (грязно, но работает) или просто пропускаем запись
        # Лучше просто вызвать поиск снова
        pass 
    await search_profiles(cb.message, None)

# --- РЕДАКТИРОВАНИЕ ПРОФИЛЯ ---
@dp.callback_query(F.data == "edit_qotd")
async def edit_qotd_start(cb: types.CallbackQuery, state: FSMContext):
    q = await get_qotd()
    await cb.message.answer(f"Вопрос дня: {q}\n\nНапиши свой ответ:")
    await state.set_state(EditProfile.waiting_for_input)
    await state.update_data(mode="qotd")

@dp.message(EditProfile.waiting_for_input)
async def save_profile_edit(message: types.Message, state: FSMContext):
    data = await state.get_data()
    mode = data.get("mode")
    
    async with aiosqlite.connect(DB_NAME) as db:
        if mode == "qotd":
            await db.execute("UPDATE users SET qotd_answer = ? WHERE id = ?", (message.text, message.from_user.id))
            await message.answer("Ответ сохранен! 👌")
    # ... тут можно добавить условия для других полей (edit_text, edit_tea и т.д.) ...
        await db.commit()
    
    await state.clear()
    await my_profile(message, state)

# --- MAIN ---
async def main():
    await init_db()
    print("Bot is running with COZY update 2.0 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
