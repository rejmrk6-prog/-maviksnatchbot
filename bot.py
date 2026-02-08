import asyncio
import logging
import random
import aiosqlite
from datetime import datetime, time
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- НАСТРОЙКИ ---
TOKEN = "8505098635:AAGkM2qizQkil7Lfoy3OgjYVsS320APY5HQ"  # Твой токен
ADMIN_ID = 7467909699  # Твой ID
DB_NAME = "cozy_dating.db"

# Инициализация
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
logging.basicConfig(level=logging.INFO)

# --- БАЗА ДАННЫХ ---
async def init_db():
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT,
                name TEXT,
                age INTEGER,
                bio TEXT,
                content_id TEXT,
                content_type TEXT,
                tea_pref TEXT,
                is_active INTEGER DEFAULT 1,
                is_banned INTEGER DEFAULT 0,
                is_verified INTEGER DEFAULT 0,
                report_count INTEGER DEFAULT 0,
                quiet_mode INTEGER DEFAULT 0,
                mood_today TEXT,
                last_active DATETIME
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS votes (
                from_id INTEGER,
                to_id INTEGER,
                score INTEGER,
                UNIQUE(from_id, to_id)
            )
        """)
        await db.commit()

# --- ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ---
async def can_send_notification(user_id):
    """Проверка тихого режима"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT quiet_mode FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0] == 1:
                now = datetime.now().time()
                if time(23, 0) <= now or now <= time(8, 0):
                    return False
    return True

def get_profile_link(user_id, username, name):
    if username:
        return f"@{username}"
    else:
        return f"<a href='tg://user?id={user_id}'>{name}</a>"

# --- СОСТОЯНИЯ ---
class Reg(StatesGroup):
    name = State()
    age = State()
    tea = State()
    bio = State()
    media = State()

class Mood(StatesGroup):
    status = State()

# Добавляем режим поиска, чтобы бот знал, кого показывать следующим
class SearchMode(StatesGroup):
    random = State()   # Обычный поиск
    admirers = State() # Просмотр тех, кто лайкнул

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🌸 Искать пару"), KeyboardButton(text="👤 Моя анкета")],
        [KeyboardButton(text="💘 Кто меня лайкнул"), KeyboardButton(text="💞 Взаимные")],
        [KeyboardButton(text="📓 Дневник настроения"), KeyboardButton(text="🆘 Поддержка")]
    ]
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

def get_rating_kb(target_id):
    buttons = []
    # 1-5
    buttons.append([InlineKeyboardButton(text=str(i), callback_data=f"vote_{target_id}_{i}") for i in range(1, 6)])
    # 6-10
    buttons.append([InlineKeyboardButton(text=str(i), callback_data=f"vote_{target_id}_{i}") for i in range(6, 11)])
    buttons.append([
        InlineKeyboardButton(text="💌 Пожаловаться", callback_data=f"report_{target_id}"),
        InlineKeyboardButton(text="💤 Скрыть", callback_data="skip")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

def get_profile_kb(quiet_mode):
    icon = "🔕" if quiet_mode else "🔔"
    text = "Включить тишину" if not quiet_mode else "Выключить тишину"
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{icon} {text}", callback_data="toggle_quiet")],
        [InlineKeyboardButton(text="📝 Изменить анкету", callback_data="re_register")]
    ])

# --- РЕГИСТРАЦИЯ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT id FROM users WHERE id = ?", (message.from_user.id,)) as c:
            if await c.fetchone():
                await message.answer("С возвращением! 🌿", reply_markup=get_main_menu())
                return

    await message.answer("Здравствуй! ✨\nДавай создадим твой уютный профиль.\nКак тебя зовут?", reply_markup=types.ReplyKeyboardRemove())
    await state.set_state(Reg.name)

@dp.message(Reg.name)
async def process_name(message: types.Message, state: FSMContext):
    await state.update_data(name=message.text)
    await message.answer("Сколько тебе лет? 🌿")
    await state.set_state(Reg.age)

@dp.message(Reg.age)
async def process_age(message: types.Message, state: FSMContext):
    if not message.text.isdigit():
        await message.answer("Только цифры, пожалуйста. ✨")
        return
    await state.update_data(age=int(message.text))
    await message.answer("Какой чай ты любишь? Или что заставляет тебя улыбнуться? ☕️")
    await state.set_state(Reg.tea)

@dp.message(Reg.tea)
async def process_tea(message: types.Message, state: FSMContext):
    await state.update_data(tea=message.text)
    await message.answer("Напиши пару теплых слов о себе. 📝")
    await state.set_state(Reg.bio)

@dp.message(Reg.bio)
async def process_bio(message: types.Message, state: FSMContext):
    await state.update_data(bio=message.text)
    await message.answer("А теперь пришли **фото** или **видео-кружочек**. 📸", parse_mode="Markdown")
    await state.set_state(Reg.media)

@dp.message(Reg.media)
async def process_media(message: types.Message, state: FSMContext):
    data = await state.get_data()
    content_id = None
    content_type = None

    if message.photo:
        content_id = message.photo[-1].file_id
        content_type = 'photo'
    elif message.video_note:
        content_id = message.video_note.file_id
        content_type = 'video_note'
    else:
        await message.answer("Пожалуйста, пришли фото или кружочек. 🌸")
        return

    is_verified = 1 if message.from_user.id == ADMIN_ID else 0

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (id, username, name, age, bio, tea_pref, content_id, content_type, is_verified, last_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message.from_user.id, message.from_user.username, data['name'], data['age'], data['bio'], data['tea'], content_id, content_type, is_verified, datetime.now()))
        await db.commit()
    
    await state.clear()
    
    if is_verified:
        await message.answer("Твоя анкета создана и активна! (Режим Админа)", reply_markup=get_main_menu())
    else:
        await message.answer("Анкета отправлена на проверку! Мы скоро вернемся. ⏳", reply_markup=get_main_menu())
        
        caption = f"🆕 **Новая анкета**\n{data['name']}, {data['age']}\n{data['bio']}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да", callback_data=f"approve_{message.from_user.id}"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"reject_{message.from_user.id}")
        ]])
        
        try:
            if content_type == 'photo':
                await bot.send_photo(ADMIN_ID, content_id, caption=caption, reply_markup=kb)
            else:
                await bot.send_video_note(ADMIN_ID, content_id, reply_markup=kb)
                await bot.send_message(ADMIN_ID, caption)
        except Exception as e:
            logging.error(f"Ошибка отправки админу: {e}")

# --- МОДЕРАЦИЯ ---
@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
        await db.commit()
    try:
        await bot.send_message(user_id, "Твоя анкета одобрена! Добро пожаловать. 🌸", reply_markup=get_main_menu())
    except: pass 
    await callback.answer("Анкета одобрена!")
    await callback.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    try:
        await bot.send_message(user_id, "К сожалению, фото не подошло. Попробуй другое. 😔")
    except: pass
    await callback.answer("Отклонено.")
    await callback.message.delete()

# --- ЛИЧНЫЙ КАБИНЕТ ---
@dp.message(F.text == "👤 Моя анкета")
async def my_profile_view(message: types.Message):
    my_id = message.chat.id
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE id = ?", (my_id,)) as cursor:
            user = await cursor.fetchone()
        async with db.execute("SELECT COUNT(*) FROM votes WHERE to_id = ? AND score >= 5", (my_id,)) as cursor:
            likes_count = (await cursor.fetchone())[0]

    if not user:
        await message.answer("Сначала заполни анкету! /start")
        return

    status_text = "\n⏳ <b>Статус: На проверке</b>" if user[10] == 0 else ""
    caption = (f"👤 <b>Твой профиль</b>{status_text}\n\n"
               f"Имя: {user[2]}, {user[3]}\n"
               f"О себе: {user[4]}\n"
               f"Любимое: {user[7]}\n\n"
               f"❤️ Тебя лайкнули {likes_count} раз(а).")
    kb = get_profile_kb(user[12])
    if user[6] == 'photo':
        await message.answer_photo(user[5], caption=caption, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer_video_note(user[5])
        await message.answer(caption, reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "💞 Взаимные")
async def show_mutual_likes(message: types.Message):
    my_id = message.chat.id
    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT u.name, u.username, u.id 
            FROM users u
            JOIN votes v1 ON u.id = v1.to_id 
            JOIN votes v2 ON u.id = v2.from_id
            WHERE v1.from_id = ? AND v1.score >= 5
            AND v2.to_id = ? AND v2.score >= 5
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

# --- НОВАЯ ФУНКЦИЯ: КТО МЕНЯ ЛАЙКНУЛ ---
@dp.message(F.text == "💘 Кто меня лайкнул")
async def show_who_liked_me(message: types.Message, state: FSMContext):
    my_id = message.chat.id
    
    # Устанавливаем режим "просмотра поклонников"
    await state.set_state(SearchMode.admirers)

    async with aiosqlite.connect(DB_NAME) as db:
        # Ищем людей, которые:
        # 1. Лайкнули меня (to_id = я, score >= 5)
        # 2. Которых я еще НЕ лайкал/дизлайкал (нет записи в votes где from_id = я)
        sql = """
            SELECT u.*
            FROM users u
            JOIN votes v ON u.id = v.from_id
            WHERE v.to_id = ? AND v.score >= 5
            AND u.id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)
            LIMIT 1
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            user = await cursor.fetchone()

    if not user:
        await message.answer("Пока никто новый тебя не лайкнул (или ты уже всех оценил). 🌸\nПереключаюсь на общий поиск...")
        await show_profiles(message, state) # Возвращаемся в обычный поиск
        return

    # Показываем анкету
    caption = f"💘 <b>Ты понравился этому человеку!</b>\n\n✨ <b>{user[2]}</b>, {user[3]}\n☕ {user[7]}\n📝 {user[4]}"
    kb = get_rating_kb(user[0])
    
    try:
        if user[6] == 'photo':
            await message.answer_photo(user[5], caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer_video_note(user[5])
            await message.answer(caption, reply_markup=kb, parse_mode="HTML")
    except:
        await show_who_liked_me(message, state) # Если ошибка фото, пробуем следующего

# --- ОБЫЧНЫЙ ПОИСК ---
@dp.message(F.text == "🌸 Искать пару")
async def show_profiles(message: types.Message, state: FSMContext):
    my_id = message.chat.id 
    
    # Устанавливаем режим "обычного поиска"
    await state.set_state(SearchMode.random)

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT is_verified, is_banned FROM users WHERE id = ?", (my_id,)) as c:
            me = await c.fetchone()
            if not me:
                await message.answer("Сначала нажми /start для регистрации.")
                return
            if me[0] == 0:
                await message.answer("Твоя анкета еще на проверке. ☕️")
                return
            if me[1] == 1:
                await message.answer("Аккаунт заблокирован.")
                return

        sql = """
            SELECT * FROM users 
            WHERE id != ? AND is_verified = 1 AND is_banned = 0 AND report_count < 3
            AND id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)
            ORDER BY RANDOM() LIMIT 1
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            user = await cursor.fetchone()

    if not user:
        await message.answer("Пока новых анкет нет. Загляни позже! ✨")
        return

    caption = f"✨ <b>{user[2]}</b>, {user[3]}\n\n☕ {user[7]}\n📝 {user[4]}"
    kb = get_rating_kb(user[0])
    
    try:
        if user[6] == 'photo':
            await message.answer_photo(user[5], caption=caption, reply_markup=kb, parse_mode="HTML")
        else:
            await message.answer_video_note(user[5])
            await message.answer(caption, reply_markup=kb, parse_mode="HTML")
    except:
        await show_profiles(message, state)

# --- ОБРАБОТКА ГОЛОСА (Универсальная) ---
@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery, state: FSMContext):
    _, target_id, score = callback.data.split("_")
    target_id = int(target_id)
    score = int(score)
    my_id = callback.from_user.id
    
    await callback.message.delete()
    
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO votes (from_id, to_id, score) VALUES (?, ?, ?)", (my_id, target_id, score))
            await db.commit()
        except: pass 

        if score >= 5: # Если лайк
            # Проверяем взаимность
            async with db.execute("SELECT score FROM votes WHERE from_id = ? AND to_id = ?", (target_id, my_id)) as c:
                match = await c.fetchone()
            
            # Если взаимно (или если это раздел "Кто меня лайкнул", там взаимность гарантирована)
            if match and match[0] >= 5:
                # Достаем данные
                async with db.execute("SELECT username, name FROM users WHERE id = ?", (my_id,)) as c:
                    my_data = await c.fetchone()
                    my_link = get_profile_link(my_id, my_data[0], my_data[1])
                
                async with db.execute("SELECT username, name FROM users WHERE id = ?", (target_id,)) as c:
                    target_data = await c.fetchone()
                    target_link = get_profile_link(target_id, target_data[0], target_data[1])
                
                await bot.send_message(my_id, f"💖 <b>Мэтч!</b>\nКонтакт: {target_link}", parse_mode="HTML")
                try:
                    await bot.send_message(target_id, f"💖 <b>Мэтч!</b>\nКонтакт: {my_link}", parse_mode="HTML")
                except: pass
            
            elif score == 10:
                 try:
                    await bot.send_message(target_id, "Кто-то оценил тебя на 10/10! 🔥")
                 except: pass

    # --- КУДА ИДЕМ ДАЛЬШЕ? ---
    # Проверяем текущее состояние: мы в обычном поиске или смотрим поклонников?
    current_state = await state.get_state()
    
    if current_state == SearchMode.admirers:
        await show_who_liked_me(callback.message, state) # Показываем следующего поклонника
    else:
        await show_profiles(callback.message, state) # Обычный поиск

@dp.callback_query(F.data == "skip")
async def skip_profile(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.delete()
    current_state = await state.get_state()
    
    if current_state == SearchMode.admirers:
        await show_who_liked_me(callback.message, state)
    else:
        await show_profiles(callback.message, state)

@dp.callback_query(F.data.startswith("report_"))
async def report_user(callback: types.CallbackQuery, state: FSMContext):
    bad_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET report_count = report_count + 1 WHERE id = ?", (bad_id,))
        await db.commit()
    await callback.answer("Жалоба отправлена.", show_alert=True)
    await callback.message.delete()
    
    # Возвращаемся в нужный режим
    current_state = await state.get_state()
    if current_state == SearchMode.admirers:
        await show_who_liked_me(callback.message, state)
    else:
        await show_profiles(callback.message, state)

@dp.callback_query(F.data == "re_register")
async def re_register(callback: types.CallbackQuery, state: FSMContext):
    await callback.message.answer("Давай обновим анкету. Как тебя зовут?")
    await state.set_state(Reg.name)

@dp.callback_query(F.data == "toggle_quiet")
async def toggle_quiet_mode(callback: types.CallbackQuery):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT quiet_mode FROM users WHERE id=?", (callback.from_user.id,)) as c:
            current = (await c.fetchone())[0]
        new_status = 0 if current == 1 else 1
        await db.execute("UPDATE users SET quiet_mode = ? WHERE id = ?", (new_status, callback.from_user.id))
        await db.commit()
    await callback.message.edit_reply_markup(reply_markup=get_profile_kb(new_status))

@dp.message(F.text == "🆘 Поддержка")
async def sos(message: types.Message):
    await message.answer("Если что-то случилось — не переживай. Ты можешь пожаловаться на пользователя в анкете. 🛡")

@dp.message(F.text == "📓 Дневник настроения")
async def mood_diary(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("Какая погода у тебя в душе? 🌦")
    await state.set_state(Mood.status)

@dp.message(Mood.status)
async def process_mood(message: types.Message, state: FSMContext):
    await message.answer("Записал в дневник. 🫂")
    await state.clear()

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c: u = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM votes") as c: l = (await c.fetchone())[0]
    await message.answer(f"📊 Юзеров: {u}\n❤️ Лайков: {l}")

async def main():
    await init_db()
    print("Mavics Bot с разделом 'Кто меня лайкнул' запущен! 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())