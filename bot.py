import asyncio
import logging
import aiosqlite
from datetime import datetime, time
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# --- НАСТРОЙКИ ---
TOKEN = "8505098635:AAGkM2qizQkil7Lfoy3OgjYVsS320APY5HQ" 
ADMIN_ID = 7467909699 
DB_NAME = "cozy_dating.db"

# Инициализация
bot = Bot(token=TOKEN)
dp = Dispatcher()
scheduler = AsyncIOScheduler()
logging.basicConfig(level=logging.INFO)

# --- ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ ДЛЯ ЧАТА ---
chat_queue = [] # Очередь поиска собеседника
active_chats = {} # Словарь: user_id -> partner_id

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
async def get_user_data(user_id):
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT * FROM users WHERE id = ?", (user_id,)) as cursor:
            return await cursor.fetchone()

async def can_send_notification(user_id):
    """Проверка тихого режима"""
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT quiet_mode FROM users WHERE id = ?", (user_id,)) as cursor:
            row = await cursor.fetchone()
            if row and row[0] == 1:
                now = datetime.now().time()
                # Тихий режим с 23:00 до 08:00
                if time(23, 0) <= now or now <= time(8, 0):
                    return False
    return True

def get_profile_link(user_id, username, name):
    """Создает красивую ссылку"""
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

class ChatState(StatesGroup):
    in_chat = State()

# --- КЛАВИАТУРЫ ---
def get_main_menu():
    kb = [
        [KeyboardButton(text="🌸 Искать пару"), KeyboardButton(text="🗣 Анонимный чат")],
        [KeyboardButton(text="👤 Моя анкета"), KeyboardButton(text="💌 Симпатии")],
        [KeyboardButton(text="✨ Комплимент"), KeyboardButton(text="📓 Дневник настроения")],
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

def get_chat_kb():
    return ReplyKeyboardMarkup(keyboard=[[KeyboardButton(text="🛑 Остановить диалог")]], resize_keyboard=True)

# --- РЕГИСТРАЦИЯ И СТАРТ ---
@dp.message(Command("start"))
async def cmd_start(message: types.Message, state: FSMContext):
    user = await get_user_data(message.from_user.id)
    
    # Если пользователь уже есть в базе
    if user:
        # Если анкета забанена
        if user[9] == 1: 
            await message.answer("Доступ ограничен. ⛔️")
            return
            
        await message.answer("С возвращением! ✨\nМы скучали.", reply_markup=get_main_menu())
        # Сразу предлагаем искать пару, если анкета верифицирована
        if user[10] == 1:
            await show_profiles(message)
    else:
        # Если новый пользователь
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
        
        if content_type == 'photo':
            await bot.send_photo(ADMIN_ID, content_id, caption=caption, reply_markup=kb)
        else:
            await bot.send_video_note(ADMIN_ID, content_id, reply_markup=kb)
            await bot.send_message(ADMIN_ID, caption)

# --- МОДЕРАЦИЯ ---
@dp.callback_query(F.data.startswith("approve_"))
async def admin_approve(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET is_verified = 1 WHERE id = ?", (user_id,))
        await db.commit()
    
    try:
        await bot.send_message(user_id, "Твоя анкета одобрена! Добро пожаловать. 🌸")
    except:
        pass 
    await callback.answer("Анкета одобрена!")
    await callback.message.edit_reply_markup(reply_markup=None)

@dp.callback_query(F.data.startswith("reject_"))
async def admin_reject(callback: types.CallbackQuery):
    user_id = int(callback.data.split("_")[1])
    try:
        await bot.send_message(user_id, "К сожалению, фото не подошло. Попробуй другое. 😔")
    except:
        pass
    await callback.answer("Отклонено.")
    await callback.message.delete()

# --- ЛИЧНЫЙ КАБИНЕТ И СИМПАТИИ ---
@dp.message(F.text == "👤 Моя анкета")
async def my_profile_view(message: types.Message):
    user = await get_user_data(message.from_user.id)
    if not user:
        await message.answer("Сначала заполни анкету! /start")
        return

    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM votes WHERE to_id = ? AND score >= 6", (message.from_user.id,)) as cursor:
            likes_count = (await cursor.fetchone())[0]

    status_text = ""
    if user[10] == 0:
        status_text = "\n⏳ <b>Статус: На проверке</b> (другие тебя не видят)"
    
    caption = (f"👤 <b>Твой профиль</b>{status_text}\n\n"
               f"Имя: {user[2]}, {user[3]}\n"
               f"О себе: {user[4]}\n"
               f"Важное: {user[7]}\n\n"
               f"❤️ Тебя лайкнули {likes_count} раз(а).")

    kb = get_profile_kb(user[12])
    
    if user[6] == 'photo':
        await message.answer_photo(user[5], caption=caption, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer_video_note(user[5])
        await message.answer(caption, reply_markup=kb, parse_mode="HTML")

@dp.message(F.text == "💌 Симпатии")
async def show_likes(message: types.Message):
    my_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        # Ищем тех, кто лайкнул меня (score >= 6), но кого я еще не оценивал
        sql = """
            SELECT u.* FROM users u
            JOIN votes v ON u.id = v.from_id
            WHERE v.to_id = ? AND v.score >= 6
            AND u.id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)
            LIMIT 1
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            user = await cursor.fetchone()
    
    if not user:
        await message.answer("Новых симпатий пока нет. Но скоро обязательно появятся! ✨")
        return

    await message.answer("Кое-кто тобой заинтересовался! 👇")
    # Используем стандартный показ профиля
    caption = f"✨ <b>{user[2]}</b>, {user[3]}\n\n☕ {user[7]}\n📝 {user[4]}\n\n<i>Этот человек лайкнул тебя!</i>"
    kb = get_rating_kb(user[0])
    
    if user[6] == 'photo':
        await message.answer_photo(user[5], caption=caption, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer_video_note(user[5])
        await message.answer(caption, reply_markup=kb, parse_mode="HTML")

# --- ПОИСК И ГОЛОСОВАНИЕ ---
@dp.message(F.text == "🌸 Искать пару")
async def show_profiles(message: types.Message):
    my_id = message.from_user.id
    async with aiosqlite.connect(DB_NAME) as db:
        # Проверка статуса
        async with db.execute("SELECT is_verified, is_banned FROM users WHERE id = ?", (my_id,)) as c:
            me = await c.fetchone()
            if not me:
                await message.answer("Сначала нажми /start")
                return
            if me[0] == 0:
                await message.answer("Твоя анкета еще проверяется модератором. Подожди немного... ☕️")
                return
            if me[1] == 1:
                await message.answer("Поиск недоступен.")
                return

        # Поиск случайной анкеты
        sql = """
            SELECT * FROM users 
            WHERE id != ? AND is_verified = 1 AND is_banned = 0 AND report_count < 3
            AND id NOT IN (SELECT to_id FROM votes WHERE from_id = ?)
            ORDER BY RANDOM() LIMIT 1
        """
        async with db.execute(sql, (my_id, my_id)) as cursor:
            user = await cursor.fetchone()

    if not user:
        await message.answer("Пока новых анкет нет. Загляни попозже! ✨")
        return

    caption = f"✨ <b>{user[2]}</b>, {user[3]}\n\n☕ {user[7]}\n📝 {user[4]}"
    kb = get_rating_kb(user[0])
    
    if user[6] == 'photo':
        await message.answer_photo(user[5], caption=caption, reply_markup=kb, parse_mode="HTML")
    else:
        await message.answer_video_note(user[5])
        await message.answer(caption, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data.startswith("vote_"))
async def process_vote(callback: types.CallbackQuery):
    _, target_id, score = callback.data.split("_")
    target_id = int(target_id)
    score = int(score)
    my_id = callback.from_user.id
    
    await callback.message.delete()
    
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("INSERT OR REPLACE INTO votes (from_id, to_id, score) VALUES (?, ?, ?)", (my_id, target_id, score))
        await db.commit()
        
        # ЛОГИКА МЭТЧА (6+)
        if score >= 6:
            # Проверяем ответный лайк
            async with db.execute("SELECT score FROM votes WHERE from_id = ? AND to_id = ?", (target_id, my_id)) as c:
                match = await c.fetchone()
            
            if match and match[0] >= 6:
                # --- ПОЛУЧАЕМ ДАННЫЕ ---
                async with db.execute("SELECT username, name FROM users WHERE id = ?", (my_id,)) as c:
                    my_data = await c.fetchone()
                    my_name = my_data[1]
                    my_link = get_profile_link(my_id, my_data[0], my_data[1])
                
                async with db.execute("SELECT username, name FROM users WHERE id = ?", (target_id,)) as c:
                    target_data = await c.fetchone()
                    target_name = target_data[1]
                    target_link = get_profile_link(target_id, target_data[0], target_data[1])

                is_gold = (score == 10 and match[0] == 10)
                
                # Сообщение МНЕ
                txt_me = "🌟 <b>ЗОЛОТОЙ МЭТЧ!</b>" if is_gold else "✨ <b>Взаимная симпатия!</b>"
                await bot.send_message(
                    my_id, 
                    f"{txt_me}\nСкорее пиши: {target_link}\n(Если ссылка не открывается, нажми /start, чтобы обновить базу)", 
                    parse_mode="HTML"
                )
                
                # Сообщение ЕМУ
                if await can_send_notification(target_id):
                    txt_he = "🌟 <b>ЗОЛОТОЙ МЭТЧ!</b>" if is_gold else "Кажется, чье-то сердце отозвалось... ✨"
                    try:
                        await bot.send_message(
                            target_id, 
                            f"{txt_he}\nПосмотришь? {my_link}\n\n<i>Нажмите /start, если бот молчал.</i>", 
                            parse_mode="HTML"
                        )
                    except:
                        pass # Бот заблокирован пользователем

            elif score == 10:
                 # Просто лайк 10, но пока не взаимно
                 if await can_send_notification(target_id):
                    try:
                        await bot.send_message(target_id, "Кто-то оценил твою анкету на 10/10! Твоя магия работает. ✨")
                    except: pass

    # Показываем следующего
    await show_profiles(callback.message)

# --- АНОНИМНЫЙ ЧАТ ---
async def stop_chat_timer(user1_id, user2_id):
    """Таймер на 5 минут"""
    await asyncio.sleep(300) # 300 секунд = 5 минут
    
    # Проверяем, находятся ли они все еще в чате
    if active_chats.get(user1_id) == user2_id:
        # Разрываем соединение
        active_chats.pop(user1_id, None)
        active_chats.pop(user2_id, None)
        
        kb = get_main_menu()
        msg = "⏰ Время вышло (5 минут). Надеюсь, вам было тепло.\nВы можете найти нового собеседника."
        
        try: await bot.send_message(user1_id, msg, reply_markup=kb)
        except: pass
        try: await bot.send_message(user2_id, msg, reply_markup=kb)
        except: pass

@dp.message(F.text == "🗣 Анонимный чат")
async def anon_chat_start(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    
    if user_id in active_chats:
        await message.answer("Ты уже в диалоге! Напиши что-нибудь.", reply_markup=get_chat_kb())
        return
        
    if user_id in chat_queue:
        chat_queue.remove(user_id)
        await message.answer("Поиск остановлен. 🛑", reply_markup=get_main_menu())
        return

    # Если в очереди есть кто-то
    if len(chat_queue) > 0:
        partner_id = chat_queue.pop(0)
        
        # Соединяем
        active_chats[user_id] = partner_id
        active_chats[partner_id] = user_id
        
        # Запускаем таймер
        asyncio.create_task(stop_chat_timer(user_id, partner_id))
        
        kb = get_chat_kb()
        msg = "🗣 <b>Собеседник найден!</b>\nУ вас есть 5 минут, чтобы поговорить по душам.\n\n<i>Анонимно. Уютно.</i>"
        
        await message.answer(msg, reply_markup=kb, parse_mode="HTML")
        await bot.send_message(partner_id, msg, reply_markup=kb, parse_mode="HTML")
        
        # Устанавливаем состояния
        await state.set_state(ChatState.in_chat)
        # Для партнера нужно найти его context (сложнее без объекта, но можно просто проверять фильтром)
        
    else:
        chat_queue.append(user_id)
        await message.answer("🔍 Ищем собеседника... (Нажми еще раз, чтобы отменить)", reply_markup=get_main_menu())

@dp.message(F.text == "🛑 Остановить диалог")
async def stop_chat_manual(message: types.Message, state: FSMContext):
    user_id = message.from_user.id
    if user_id in active_chats:
        partner_id = active_chats[user_id]
        
        active_chats.pop(user_id, None)
        active_chats.pop(partner_id, None)
        
        await message.answer("Диалог завершен. 🍂", reply_markup=get_main_menu())
        await state.clear()
        
        try:
            await bot.send_message(partner_id, "Собеседник покинул чат. 🍂", reply_markup=get_main_menu())
        except: pass
    else:
        # Если вдруг нажал кнопку, но чата нет
        if user_id in chat_queue: chat_queue.remove(user_id)
        await message.answer("Диалогов нет.", reply_markup=get_main_menu())

# Обработка сообщений внутри чата (Фильтр: если юзер в active_chats)
@dp.message(lambda m: m.from_user.id in active_chats and m.text != "🛑 Остановить диалог")
async def chat_relay(message: types.Message):
    user_id = message.from_user.id
    partner_id = active_chats.get(user_id)
    
    if not partner_id:
        return

    # Пересылка разных типов контента
    try:
        if message.text:
            await bot.send_message(partner_id, f"🗣 <b>Собеседник:</b>\n{message.text}", parse_mode="HTML")
        elif message.photo:
            await bot.send_photo(partner_id, message.photo[-1].file_id, caption="🗣 Фото от собеседника")
        elif message.sticker:
            await bot.send_sticker(partner_id, message.sticker.file_id)
        elif message.video_note:
            await bot.send_video_note(partner_id, message.video_note.file_id)
        elif message.voice:
            await bot.send_voice(partner_id, message.voice.file_id)
        else:
            await bot.send_message(partner_id, "🗣 <i>Собеседник прислал что-то, что я не могу отобразить.</i>", parse_mode="HTML")
    except Exception as e:
        await message.answer(f"Не удалось отправить сообщение. Возможно, собеседник заблокировал бота. Диалог завершен.")
        # Разрыв соединения при ошибке
        active_chats.pop(user_id, None)
        active_chats.pop(partner_id, None)

# --- ПРОЧЕЕ ---
@dp.callback_query(F.data.startswith("report_"))
async def report_user(callback: types.CallbackQuery):
    bad_id = int(callback.data.split("_")[1])
    async with aiosqlite.connect(DB_NAME) as db:
        await db.execute("UPDATE users SET report_count = report_count + 1 WHERE id = ?", (bad_id,))
        await db.commit()
    await callback.answer("Жалоба принята.", show_alert=True)
    await show_profiles(callback.message)

@dp.callback_query(F.data == "skip")
async def skip_profile(callback: types.CallbackQuery):
    await callback.message.delete()
    await show_profiles(callback.message)

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

@dp.message(F.text == "✨ Комплимент")
async def send_compliment(message: types.Message):
    await message.answer("Ты — причина чьей-то улыбки сегодня. ✨")

@dp.message(F.text == "📓 Дневник настроения")
async def mood_diary(message: types.Message, state: FSMContext):
    await message.answer("Какая погода у тебя в душе? 🌦")
    await state.set_state(Mood.status)

@dp.message(Mood.status)
async def process_mood(message: types.Message, state: FSMContext):
    await message.answer("Спасибо, что поделился. 🫂")
    await state.clear()

@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID: return
    async with aiosqlite.connect(DB_NAME) as db:
        async with db.execute("SELECT COUNT(*) FROM users") as c: u = (await c.fetchone())[0]
        async with db.execute("SELECT COUNT(*) FROM votes WHERE score >= 6") as c: l = (await c.fetchone())[0]
    
    # Статистика чата
    chat_stats = f"\n🗣 В чате сейчас: {len(active_chats)//2} пар"
    await message.answer(f"📊 Юзеров: {u}\n❤️ Лайков: {l}{chat_stats}")

async def main():
    await init_db()
    print("Бот работает! 🚀")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())