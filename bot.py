import os
import logging
from datetime import datetime

import requests
from PIL import Image
import asyncio
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

API_TOKEN = os.environ.get("BOT_TOKEN", "8609460139:AAHY3lbEomepYMEfDbeEE3mIRoiw9qfMRlc")

MD_API = "https://api.mangadex.org"
MD_COVER_URL = "https://uploads.mangadex.org/covers"
CHUNK_SIZE = 8192
MAX_RETRIES = 3

user_sessions = {}
downloads_dir = "/tmp/downloads"

os.makedirs(downloads_dir, exist_ok=True)

def sanitize_folder_name(name):
    return "".join(c if c.isalnum() else "_" for c in name)


def get_manga_pages(chapter_id):
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(
                f"{MD_API}/at-home/server/{chapter_id}",
                timeout=20
            )
            if response.status_code == 200:
                data = response.json()
                base_url = data.get("baseUrl", "")
                chapter = data.get("chapter", {})
                hash_val = chapter.get("hash", "")
                pages = chapter.get("data", [])
                return [
                    f"{base_url}/data/{hash_val}/{page}"
                    for page in pages
                ]
            elif response.status_code == 404:
                return []
        except Exception as e:
            logger.error(f"Error: {e}")
            if attempt < MAX_RETRIES - 1:
                continue
    return None


def search_manga(query, offset=0):
    try:
        params = {
            "title": query,
            "limit": 10,
            "offset": offset,
            "includes[]": ["cover_art", "author", "artist"],
            "contentRating[]": ["safe", "suggestive", "erotica"]
        }
        response = requests.get(f"{MD_API}/manga", params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for m in data.get("data", []):
            attrs = m.get("attributes", {})
            relationships = m.get("relationships", [])
            
            title = attrs.get("title", {})
            name = title.get("ru") or title.get("en") or title.get("ja") or list(title.values())[0] if title else "Unknown"
            
            cover_file = ""
            for rel in relationships:
                if rel.get("type") == "cover_art":
                    cover_file = rel.get("attributes", {}).get("fileName", "")
            
            manga_id = m.get("id")
            cover_url = f"{MD_COVER_URL}/{manga_id}/{cover_file}.512.jpg" if cover_file else ""
            
            author = ""
            for rel in relationships:
                if rel.get("type") == "author":
                    author = rel.get("attributes", {}).get("name", "")
            
            results.append({
                "id": manga_id,
                "name": name,
                "cover_url": cover_url,
                "author": author
            })
        
        return results
    except Exception as e:
        logger.error(f"Search error: {e}")
        return []


def get_chapters(manga_id):
    try:
        params = {
            "limit": 100,
            "includes[]": ["user"],
            "translatedLanguage[]": ["ru", "en"]
        }
        response = requests.get(f"{MD_API}/manga/{manga_id}/feed", params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        chapters = []
        for ch in data.get("data", []):
            attrs = ch.get("attributes", {})
            vol = attrs.get("volume") or ""
            ch_num = attrs.get("chapter") or ""
            title = attrs.get("title") or ""
            chapters.append({
                "id": ch.get("id"),
                "volume": vol,
                "chapter": ch_num,
                "title": title
            })
        
        chapters.sort(key=lambda x: (x.get("volume") or "0", x.get("chapter") or "0"))
        return chapters
    except Exception as e:
        logger.error(f"Get chapters error: {e}")
        return []


def download_chapter(chapter_id, save_dir):
    logger.info(f"Downloading chapter: {chapter_id}")
    
    page_urls = get_manga_pages(chapter_id)
    if not page_urls:
        return None
    
    os.makedirs(save_dir, exist_ok=True)
    
    for i, url in enumerate(page_urls, start=1):
        try:
            response = requests.get(url, stream=True, timeout=20)
            if response.status_code == 200:
                image_path = os.path.join(save_dir, f"{i:03}.jpg")
                with open(image_path, "wb") as f:
                    for chunk in response.iter_content(CHUNK_SIZE):
                        f.write(chunk)
        except Exception as e:
            logger.error(f"Error downloading page {i}: {e}")
            continue
    
    return page_urls


def create_pdf(image_paths, output_path):
    try:
        images = []
        for img_path in sorted(image_paths):
            try:
                img = Image.open(img_path).convert("RGB")
                images.append(img)
            except:
                continue
        
        if images:
            images[0].save(output_path, save_all=True, append_images=images[1:])
            return True
    except Exception as e:
        logger.error(f"PDF error: {e}")
    return False


dp = Dispatcher()


@dp.message(Command("start"))
async def cmd_start(message: types.Message):
    await message.answer(
        "🔍 <b>MangaDex Bot</b>\n\n"
        "Команды:\n"
        "/search <название> - Поиск манги\n"
        "/help - Помощь"
    )


@dp.message(Command("help"))
async def cmd_help(message: types.Message):
    await message.answer(
        "<b>Команды:</b>\n\n"
        "/search <название> - Поиск манги\n"
        "/chapters <manga_id> - Список глав\n"
        "/download <chapter_id> - Скачать главу в PDF\n"
        "/cancel - Отмена операции\n\n"
        "<b>Как скачать:</b>\n"
        "1. /search Naruto\n"
        "2. Выберите мангу (нажмите кнопку)\n"
        "3. Выберите главу\n"
        "4. Получите PDF в чат"
    )


@dp.message(Command("search"))
async def cmd_search(message: types.Message):
    query = message.text.replace("/search", "").strip()
    if not query:
        await message.answer("Введите название: /search Naruto")
        return
    
    await message.answer(f"🔍 Ищу: {query}...")
    
    results = search_manga(query)
    if not results:
        await message.answer("Ничего не найдено 😢")
        return
    
    user_sessions[message.from_user.id] = {"results": results, "step": "select_manga"}
    
    keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
        [types.InlineKeyboardButton(text=f"{i+1}. {m['name'][:35]}", callback_data=f"manga:{m['id']}:{m['name']}")]
        for i, m in enumerate(results)
    ])
    
    await message.answer(f"Найдено {len(results)} манги:", reply_markup=keyboard)


@dp.callback_query()
async def callback_handler(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    data = callback.data
    
    if data.startswith("manga:"):
        parts = data.split(":")
        manga_id = parts[1]
        manga_name = ":".join(parts[2:])
        
        await callback.message.answer(f"📚 Загружаю главы: {manga_name}...")
        
        chapters = get_chapters(manga_id)
        if not chapters:
            await callback.message.answer("Главы не найдены 😢")
            await callback.answer()
            return
        
        user_sessions[user_id] = {
            "manga_id": manga_id,
            "manga_name": manga_name,
            "chapters": chapters,
            "step": "select_chapter"
        }
        
        keyboard = types.InlineKeyboardMarkup(inline_keyboard=[
            [types.InlineKeyboardButton(text=f"Глава {ch.get('chapter') or '?'}", callback_data=f"chapter:{ch['id']}")]
            for ch in chapters[:20]
        ])
        
        await callback.message.answer(
            f"Выберите главу ({len(chapters)} всего):",
            reply_markup=keyboard
        )
        await callback.answer()
    
    elif data.startswith("chapter:"):
        if user_id not in user_sessions:
            await callback.message.answer("Начните заново /search")
            await callback.answer()
            return
        
        session = user_sessions[user_id]
        chapter_id = data.split(":")[1]
        manga_name = session.get("manga_name", "Manga")
        
        await callback.message.answer(f"📥 Скачиваю главу {chapter_id}...")
        
        save_dir = f"{downloads_dir}/{sanitize_folder_name(manga_name)}/{chapter_id}"
        
        page_urls = download_chapter(chapter_id, save_dir)
        if not page_urls:
            await callback.message.answer("Не удалось скачать 😢")
            await callback.answer()
            return
        
        pdf_path = f"{save_dir}/{chapter_id}.pdf"
        image_files = [os.path.join(save_dir, f) for f in sorted(os.listdir(save_dir)) if f.endswith('.jpg')]
        create_pdf(image_files, pdf_path)
        
        if os.path.exists(pdf_path):
            await callback.message.answer("Готово! Отправляю PDF...")
            await callback.message.answer_document(
                types.FSInputFile(pdf_path),
                caption=f"📖 {manga_name} - Глава {chapter_id}"
            )
        else:
            await callback.message.answer("Ошибка создания PDF 😢")
        
        await callback.answer()


@dp.message(Command("cancel"))
async def cmd_cancel(message: types.Message):
    user_id = message.from_user.id
    if user_id in user_sessions:
        del user_sessions[user_id]
        await message.answer("❌ Отменено")
    else:
        await message.answer("Нечего отменять")


@dp.message()
async def echo(message: types.Message):
    if message.text and message.text.startswith("/"):
        await message.answer("Неизвестная команда. /help для с��равки")
    else:
        await cmd_search(message)


async def main():
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    
    logger.info("Bot started!")
    
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())