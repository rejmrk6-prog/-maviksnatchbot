import asyncio
import logging
import json
import aiosqlite
from datetime import datetime, time
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    InlineKeyboardMarkup, InlineKeyboardButton, 
    ReplyKeyboardMarkup, KeyboardButton, 
    InputMediaPhoto, ReplyKeyboardRemove
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- НАСТРОЙКИ ---
TOKEN = "8505098635:AAGkM2qizQkil7Lfoy3OgjYVsS320APY5HQQ"  # Твой токен
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
                content_ids TEXT,  -- Изменили название, тут будет JSON список
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
def get_profile_link(user_id, username, name):
    if username:
        return f"@{username}"
    else:
        return f"<a href='tg://user?id={user_id}'>{name}</a>"

async def send_user_profile(chat_id, user_data, is_match=False, match_with_me=False):
    """
    Универсальная функция отправки анкеты.
    user_data: кортеж данных из БД
    is_match: Если True, показываем как уведомление о мэтче (без кнопок оценки)
    match_with_me: Если True, значит это моя анкета (или просмотр лайкнувшего)
    """
    uid, username, name, age, bio, content_ids_raw, c_type, tea_pref = user_data[0], user_data[1], user_data[2], user_data[3], user_data[4], user_data[5], user_data[6], user_data[7]
    quiet = user_data[12]
    
    # Декодируем медиа (поддержка старого формата строки и нового JSON)
    try:
        media_files = json.loads(content_ids_raw)
        if not isinstance(media_files, list):
            media_files = [content_ids_raw]
    except:
        media_files = [content_ids_raw]

    # Формируем текст
    if is_match:
        header = f"💖 <b>ЭТО ВЗАИМНО!</b>\nКонтакт: {get_profile_link(uid, username, name)}\n"
        kb = None # Кнопок оценки нет при мэтче
    else:
        header = f"✨ <b>{name}</b>, {age}\n"
        if match_with_me: # Это просмотр своей анкеты
             kb = get_profile_kb(quiet)
        else: # Это поиск
             kb = get_rating_kb(uid)

    caption = f"{header}\n☕ {tea_pref}\n📝 {bio}"

    try:
        if c_type == 'video_note':
            await bot.send_video_note(chat_id, media_files[0])
            await bot.send_message(chat_id, caption, reply_markup=kb, parse_mode="HTML")
        
        elif c_type == 'photo':
            if len(media_files) == 1:
                # Одно фото - шлем с подписью
                await bot.send_photo(chat_id, media_files[0], caption=caption, reply_markup=kb, parse_mode="HTML")
            else:
                # Несколько фото - шлем альбом + отдельное сообщение с текстом и кнопками
                media_group = [InputMediaPhoto(media=file_id) for file_id in media_files]
                await bot.send_media_group(chat_id, media=media_group)
                await bot.send_message(chat_id, caption, reply_markup=kb, parse_mode="HTML")
    except Exception as e:
        logging.error(f"Ошибка отправки профиля {uid}: {e}")
        # Если медиа недоступно, шлем текст
        await bot.send_message(chat_id, f"[Ошибка медиа]\n{caption}", reply_markup=kb, parse_mode="HTML")

# --- СОСТОЯНИЯ ---
class Reg(StatesGroup):
    name = State()
    age = State()
    tea = State()
    bio = State()
    media = State() # Тут цикл загрузки фото

class Mood(StatesGroup):
    status = State()

class AdminContact(StatesGroup):
    message = State()

class SearchMode(StatesGroup):
    random = State()   
    admirers = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🌸 Искать пару"), KeyboardButton(text="👤 Моя анкета")],
        [KeyboardButton(text="💘 Кто меня лайкнул"), KeyboardButton(text="💞 Взаимные")],
        [KeyboardButton(text="📓 Дневник настроения"), KeyboardButton(text="📞 Связь с админом")]
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

def get_done_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="✅ Готово")]], resize_keyboard=True)

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
    await state.update_data(bio=message.text, photos=[])
    await message.answer("Теперь пришли **до 3-х фото** или **1 видео-кружочек**. 📸\nОтправляй по одному, а когда закончишь — нажми кнопку ниже.", 
                         parse_mode="Markdown", reply_markup=get_done_kb())
    await state.set_state(Reg.media)

@dp.message(Reg.media)
async def process_media(message: types.Message, state: FSMContext):
    data = await state.get_data()
    photos = data.get("photos", [])

    # Если нажали "Готово"
    if message.text == "✅ Готово":
        if not photos:
            await message.answer("Пришли хотя бы одну фотографию! 🌸")
            return
        await finish_registration(message, state, photos, "photo")
        return

    # Если видео-кружочек
    if message.video_note:
        await finish_registration(message, state, [message.video_note.file_id], "video_note")
        return

    # Если фото
    if message.photo:
        file_id = message.photo[-1].file_id
        photos.append(file_id)
        await state.update_data(photos=photos)
        
        count = len(photos)
        if count >= 3:
            await finish_registration(message, state, photos, "photo")
        else:
            await message.answer(f"Загружено фото: {count} из 3. Можешь отправить ещё или нажать '✅ Готово'.")
        return
    
    await message.answer("Пожалуйста, пришли фото или кружочек. 🌸")

async def finish_registration(message, state, content_ids, content_type):
    data = await state.get_data()
    is_verified = 1 if message.from_user.id == ADMIN_ID else 0
    
    # Сериализуем список ID в JSON
    content_json = json.dumps(content_ids)

    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("""
            INSERT OR REPLACE INTO users (id, username, name, age, bio, tea_pref, content_ids, content_type, is_verified, last_active)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (message.from_user.id, message.from_user.username, data['name'], data['age'], data['bio'], data['tea'], content_json, content_type, is_verified, datetime.now()))
        await db.commit()
    
    await state.clear()
    
    if is_verified:
        await message.answer("Твоя анкета создана! (Админ)", reply_markup=get_main_menu())
    else:
        await message.answer("Анкета отправлена на проверку! Мы скоро вернемся. ⏳", reply_markup=get_main_menu())
        
        # Отправка админу на проверку
        caption = f"🆕 **Новая анкета**\n{data['name']}, {data['age']}\n{data['bio']}"
        kb = InlineKeyboardMarkup(inline_keyboard=[[
            InlineKeyboardButton(text="✅ Да", callback_data=f"approve_{message.from_user.id}"),
            InlineKeyboardButton(text="❌ Нет", callback_data=f"reject_{message.from_user.id}")
        ]])
        
        try:
            if content_type == 'photo':
                if len(content_ids) == 1:
                    await bot.send_photo(ADMIN_ID, content_ids[0], caption=caption, reply_markup=kb)
                else:
                    # Админу покажем только первое фото для кнопки, чтобы не спамить альбомами
                    await bot.send_photo(ADMIN_ID, content_ids[0], caption=caption + "\n(Есть еще фото)", reply_markup=kb)
            else:
                await bot.send_video_note(ADMIN_ID, content_ids[0], reply_markup=kb)
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
        await bot.send_message(user_id, "К сожалению, анкета не подошла. Попробуй изменить фото или описание. 😔")
    except: pass
    await callback.answer("Отклонено.")
    await callback.message.delete()

# --- ЛИЧНЫЙ КАБИНЕТ ---
@dp.message(F.text == "👤 Моя анкета")
async def my_profile_view(message: types.Message):
    my_id = message.chat.id
    async with aiosqlite.connect(DB_NAME) as db:
        # Получаем всё, включая новый content_ids
        async with db.execute("SELECT id, username, name, age, bio, content_ids, content_type, tea_pref, 0, 0, is_verified, 0, quiet_mode FROM users WHERE id = ?", (my_id,)) as cursor:
            user = await cursor.fetchone()
        async with db.execute("SELECT COUNT(*) FROM votes WHERE to_id = ? AND score >= 5", (my_id,)) as cursor:
            likes_count = (await cursor.fetchone())[0]

    if not user:
        await message.answer("Сначала заполни анкету! /start")
        return

    if user[10] == 0:
        await message.answer("⏳ <b>Статус: На проверке</b>", parse_mode="HTML")

    await send_user_profile(my_id, user, is_match=False, match_with_me=True)
    await message.answer(f"❤️ Тебя лайкнули {likes_count} раз(а).")


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

# --- КТО МЕНЯ ЛАЙКНУЛ ---
@dp.message(F.text == "💘 Кто меня лайкнул")
async def show_who_liked_me(message: types.Message, state: FSMContext):
    my_id = message.chat.id
    await state.set_state(SearchMode.admirers)

    async with aiosqlite.connect(DB_NAME) as db:
        sql = """
            SELECT id, username, name, age, bio, content_ids, content_type, tea_pref, 
                   is_active, is_banned, is_verified, report_count, quiet_mode 
            FROM users u
            JOIN votes v ON u.id = v.from_id
            WHERE v.to_id = ? AND v.score >= 5
            AND u.id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)
            LIMIT 1
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            user = await cursor.fetchone()

    if not user:
        await message.answer("Пока никто новый тебя не лайкнул. 🌸\nПереключаюсь на общий поиск...")
        await show_profiles(message, state)
        return

    await message.answer("💘 <b>Ты понравился этому человеку!</b>", parse_mode="HTML")
    await send_user_profile(my_id, user, is_match=False, match_with_me=False)

# --- ОБЫЧНЫЙ ПОИСК ---
@dp.message(F.text == "🌸 Искать пару")
async def show_profiles(message: types.Message, state: FSMContext):
    my_id = message.chat.id 
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
            SELECT id, username, name, age, bio, content_ids, content_type, tea_pref, 
                   is_active, is_banned, is_verified, report_count, quiet_mode 
            FROM users 
            WHERE id != ? AND is_verified = 1 AND is_banned = 0 AND report_count < 3
            AND id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)
            ORDER BY RANDOM() LIMIT 1
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            user = await cursor.fetchone()

    if not user:
        await message.answer("Пока новых анкет нет. Загляни позже! ✨")
        return

    await send_user_profile(my_id, user, is_match=False, match_with_me=False)

# --- ОБРАБОТКА ГОЛОСА ---
@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery, state: FSMContext):
    _, target_id, score = callback.data.split("_")
    target_id = int(target_id)
    score = int(score)
    my_id = callback.from_user.id
    
    # Удаляем сообщение с кнопками (чтобы нельзя было нажать дважды)
    await callback.message.delete()
    
    async with aiosqlite.connect(DB_NAME) as db:
        try:
            await db.execute("INSERT INTO votes (from_id, to_id, score) VALUES (?, ?, ?)", (my_id, target_id, score))
            await db.commit()
        except: pass 

        if score >= 5: # Лайк
            # Проверка взаимности
            async with db.execute("SELECT score FROM votes WHERE from_id = ? AND to_id = ?", (target_id, my_id)) as c:
                match = await c.fetchone()
            
            if match and match[0] >= 5:
                # --- ВЗАИМНОСТЬ: ПОКАЗЫВАЕМ АНКЕТЫ ДРУГ ДРУГУ ---
                
                # Загружаем мои данные
                sql_user = "SELECT id, username, name, age, bio, content_ids, content_type, tea_pref, 0, 0, 0, 0, quiet_mode FROM users WHERE id = ?"
                async with db.execute(sql_user, (my_id,)) as c:
                    my_data = await c.fetchone()
                
                # Загружаем данные партнера
                async with db.execute(sql_user, (target_id,)) as c:
                    target_data = await c.fetchone()
                
                # Отправляем мне анкету партнера
                await send_user_profile(my_id, target_data, is_match=True)
                
                # Отправляем партнеру мою анкету (если у него не ночь)
                try:
                    await send_user_profile(target_id, my_data, is_match=True)
                except: pass
            
            elif score == 10:
                 try:
                    await bot.send_message(target_id, "Кто-то оценил тебя на 10/10! 🔥")
                 except: pass

    # Идем дальше
    current_state = await state.get_state()
    if current_state == SearchMode.admirers:
        await show_who_liked_me(callback.message, state)
    else:
        await show_profiles(callback.message, state)

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

# --- СВЯЗЬ С АДМИНОМ ---
@dp.message(F.text == "📞 Связь с админом")
async def contact_admin_start(message: types.Message, state: FSMContext):
    await message.answer("Напиши свое сообщение, предложение или жалобу. Администратор получит его. 🖊\n(Для отмены введи /cancel)")
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
    print("Mavics Bot: 3 фото + Анкета при мэтче + Админ-чат запущен! 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())