import os
import logging
import telebot
from openai import OpenAI
from dotenv import load_dotenv
from collections import defaultdict, deque
from pydantic import BaseModel
import db

# Загружаем переменные окружения
load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("bot.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)

# Инициализация бота
bot = telebot.TeleBot(os.getenv("TELEGRAM_BOT_TOKEN"))

# Инициализация OpenAI клиента (через ProxiAPI)
client = OpenAI(
    api_key=os.getenv("OPENAI_API_KEY"),
    base_url=os.getenv("OPENAI_BASE_URL"),
)

# Системный промпт
SYSTEM_PROMPT = "Ты полезный AI-ассистент. Отвечай по делу."


class BotResponse(BaseModel):
    theses: list[str]
    message: str


# Словарь для хранения истории сообщений каждого пользователя
# Ключ - user_id, значение - deque с последними 20 парами (вопрос-ответ)
user_histories = defaultdict(lambda: deque(maxlen=20))


def format_context_for_prompt(user_id):
    """Форматирует контекст диалога для добавления в промпт"""
    history = user_histories[user_id]
    if not history:
        return ""

    context_lines = []
    for question, answer in history:
        context_lines.append(f"Пользователь: {question}")
        context_lines.append(f"Ассистент: {answer}")

    return "\n".join(context_lines)


@bot.message_handler(commands=["start"])
def handle_start(message):
    """Приветствие при первом запуске."""
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    logger.info(f"/start от {username} (id={user_id})")
    db.ensure_user_table(user_id)
    bot.reply_to(
        message,
        "Привет! Я AI-ассистент. Задавай любые вопросы.\n\n"
        "Доступные команды:\n"
        "/mytheses — показать все накопленные тезисы о тебе\n"
        "/reset — сбросить память (очистить контекст и тезисы)\n"
        "/start — показать это сообщение",
    )


@bot.message_handler(commands=["reset"])
def handle_reset(message):
    """Сбрасывает контекст диалога и тезисы в БД для пользователя."""
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    logger.info(f"/reset от {username} (id={user_id})")

    # Очищаем историю в памяти
    history_len = len(user_histories[user_id])
    user_histories[user_id].clear()

    # Очищаем тезисы в БД
    db_count = db.clear_theses(user_id)

    logger.info(f"Память сброшена для {username} (id={user_id}): "
                f"{history_len} сообщений из истории, {db_count} тезисов из БД")

    bot.reply_to(
        message,
        f"Память очищена.\n"
        f"Удалено сообщений из контекста: {history_len}\n"
        f"Удалено тезисов из базы данных: {db_count}\n\n"
        "Начинаем разговор с чистого листа!",
    )


@bot.message_handler(commands=["mytheses"])
def handle_mytheses(message):
    """Показывает пользователю все накопленные тезисы из БД."""
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    logger.info(f"/mytheses от {username} (id={user_id})")

    theses = db.load_theses(user_id)
    if not theses:
        bot.reply_to(message, "Тезисов пока нет. Напиши что-нибудь, и я начну их накапливать!")
        return

    lines = [f"Накопленные тезисы о тебе ({len(theses)} шт.):\n"]
    for i, thesis in enumerate(theses, 1):
        lines.append(f"{i}. {thesis}")
    bot.reply_to(message, "\n".join(lines))


@bot.message_handler(func=lambda message: True)
def handle_message(message):
    """Обработчик всех сообщений"""
    try:
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name
        user_message = message.text

        logger.info(f"Сообщение от {username} (id={user_id}): {user_message!r}")

        # Получаем контекст диалога
        context = format_context_for_prompt(user_id)
        history_len = len(user_histories[user_id])
        logger.debug(f"Контекст пользователя {user_id}: {history_len} пар в истории")

        # Формируем системный промпт с контекстом
        if context:
            system_prompt_with_context = f"{SYSTEM_PROMPT}\n\nКонтекст предыдущего диалога:\n{context}"
        else:
            system_prompt_with_context = SYSTEM_PROMPT

        # Загружаем накопленные тезисы из БД и добавляем в промпт
        db_theses = db.load_theses(user_id)
        db_theses_block = ""
        if db_theses:
            db_theses_block = (
                "\n\nТезисы из базы данных (накопленные факты о пользователе и диалоге):\n"
                + "\n".join(f"- {t}" for t in db_theses)
            )
            logger.info(f"Загружено {len(db_theses)} тезис(ов) из БД для user_{user_id}")

        logger.info(f"Отправка запроса к OpenAI для пользователя {user_id}...")

        structured_system_prompt = (
            f"{system_prompt_with_context}"
            f"{db_theses_block}\n\n"
            "В конце каждого ответа выдели основные тезисы текущего обмена "
            "в виде списка ключевых моментов для сохранения в базу данных. "
            "Тезисы должны быть краткими фактами о пользователе или теме, например: "
            "'Пользователь хочет поехать в [место]', 'Пользователь интересуется [темой]'. "
            "Отвечай строго в формате JSON с двумя ключами:\n"
            "- theses: список новых тезисов только по текущему обмену (вопрос+ответ)\n"
            "- message: твой развёрнутый ответ пользователю на его последний вопрос"
        )

        # Отправляем запрос к OpenAI с разбором структурированного ответа
        response = client.beta.chat.completions.parse(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": structured_system_prompt},
                {"role": "user", "content": user_message},
            ],
            response_format=BotResponse,
            max_tokens=1500,
            temperature=0.7,
        )

        parsed: BotResponse = response.choices[0].message.parsed
        usage = response.usage
        logger.info(
            f"Ответ получен для {user_id} | "
            f"tokens: prompt={usage.prompt_tokens}, "
            f"completion={usage.completion_tokens}, "
            f"total={usage.total_tokens}"
        )

        # Логируем тезисы в консоль (пользователь их не видит)
        logger.info(f"Новые тезисы [{user_id}]:")
        for i, thesis in enumerate(parsed.theses, 1):
            logger.info(f"  {i}. {thesis}")

        # Сохраняем новые тезисы в БД
        if parsed.theses:
            db.save_theses(user_id, parsed.theses)

        ai_response = parsed.message
        logger.debug(f"Ответ AI для {user_id}: {ai_response!r}")

        # Сохраняем пару вопрос-ответ в историю пользователя
        user_histories[user_id].append((user_message, ai_response))

        # Отправляем только message пользователю
        bot.reply_to(message, ai_response)

    except Exception as e:
        logger.error(f"Ошибка при обработке сообщения от {message.from_user.id}: {e}", exc_info=True)
        bot.reply_to(message, f"Произошла ошибка: {str(e)}")


def check_config() -> bool:
    """Проверяет наличие обязательных переменных окружения."""
    required = {
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
        "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
        "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL"),
    }
    ok = True
    for name, value in required.items():
        if not value:
            logger.error(f"Переменная окружения {name} не задана в .env")
            ok = False
        else:
            masked = value[:8] + "..." if len(value) > 8 else "***"
            logger.info(f"{name} = {masked}")
    return ok


if __name__ == "__main__":
    if not check_config():
        raise SystemExit("Запуск прерван: заполните .env файл")
    logger.info(f"OPENAI_BASE_URL: {os.getenv('OPENAI_BASE_URL')}")
    logger.info("Бот с контекстом запущен...")
    bot.infinity_polling()
