from functools import lru_cache
from pathlib import Path
from typing import Type, TypeVar
from datetime import datetime, time, timedelta
import json
import sys
import logging
import os
import time as time_module
import pandas as pd
import urllib.parse
import urllib.request
import FreedomBroker as fb
import TechnicalStrategy as ts


# Универсальный импорт для любой версии Python
try:
    import tomllib  # Для Python 3.11+
except ImportError:
    import tomli as tomllib  # Для Python 3.10 и старше

# 1. Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S',
    handlers=[
        logging.FileHandler("app.log", encoding="utf-8"),
        logging.StreamHandler()
    ]
)

# Загрузка конфигурации из TOML
try:
    with open("settings.toml", "rb") as f:
        config = tomllib.load(f)

    # Извлекаем данные из секции [credentials]
    FB_EMAIL = config["credentials"]["fb_email"]
    FB_PASSWORD = config["credentials"]["fb_password"]
    # забираем настройки стратегии
    TICKERS = config["strategy"].get("tickers", ['AIRA.KZ', 'ASBN.KZ', 'HSBK.KZ', 'KSPI.KZ'])
    _START_DATE = config["strategy"].get("start_date", '01.01.2026')
    _INTERVAL_SECONDS = config["strategy"].get("interval_seconds", 300)  # 5 мину
    _MIN_ROWS_REQUIRED = config["strategy"].get("min_rows_required", 200)
    #данные для телеграм
    TG_TOKEN = config["telegram"]["bot_token"]
    TG_CHAT_ID = config["telegram"]["chat_id"]
    # Флаг автоматического включения уведомлений
    TG_ENABLED = True
except FileNotFoundError:
    # TG_ENABLED = False
    # logging.warning("Настройки Telegram не найдены в settings.toml. Уведомления отключены.")
    logging.critical("Критическая ошибка: Файл 'settings.toml' не найден!")
    sys.exit(1)
except KeyError as e:
    logging.critical(f"Критическая ошибка: В settings.toml отсутствует обязательный параметр: {e}")
    sys.exit(1)

except Exception as e:
    logging.critical(f"Ошибка при чтении конфигурации: {e}")
    sys.exit(1)


# Константы (Конфигурация)
_START_TIME = time(11, 20)
_END_TIME = time(19, 0)
# Настройки повторных запросов
_MAX_RETRIES = 3
_RETRY_BACKOFF_FACTOR = 2  # Пауза между попытками будет расти: 2с, 4с, 8с...

all_data = {}

@lru_cache
def read_config_file() -> dict:
    config_path = Path(__file__).parent.joinpath('settings.toml')
    if not config_path.exists():
        error = "Не могу найти конфигурационный файл"
        raise ValueError(error)
    with open(config_path, 'rb') as file:
        config_data = tomlib.load(file)
    return config_data

# def get_config(model: Type[ConfigType], root_key:str) -> ConfigType:
#     pass

def send_telegram_message(text: str):
    if not TG_ENABLED:
        return

    text = text[:4000]

    # Очищаем токен от случайных пробелов
    token = str(TG_TOKEN).strip()
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": str(TG_CHAT_ID).strip(), "text": text, "parse_mode": "HTML"}

    try:
        data = json.dumps(payload).encode('utf-8')
        req = urllib.request.Request(
            url,
            data=data,
            headers={'Content-Type': 'application/json'},
            method='POST'
        )
        with urllib.request.urlopen(req, timeout=5) as response:
            if response.status == 200:
                logging.info("Уведомление в Telegram успешно отправлено!")
            else:
                logging.error(f"Ошибка Telegram API. Код ответа: {response.status}")
    except Exception as e:
        logging.error(f"Не удалось отправить уведомление в Telegram: {e}")


def get_history_data(broker: fb.FreedomBroker) -> dict:
    """Получает исторические данные до вчерашнего дня включительно."""
    yesterday = datetime.now() - timedelta(days=1)
    end_date = yesterday.strftime('%d.%m.%Y')
    history_tables = {}
    for ticker in TICKERS:
        logging.info(f"Загрузка истории для {ticker} с {_START_DATE} по {end_date}...")
        try:
            history_tables[ticker] = broker.get_history_ticker(ticker, _START_DATE, end_date, interval=5)
        except Exception as e:
            logging.error(f"Не удалось загрузить историю при старте для {ticker}: {e}")
            history_tables[ticker] = pd.DataFrame()
        # raise  # Если историю не загрузить на старте, продолжать нет смысла
    return history_tables

def get_last_row_with_retry(broker: fb.FreedomBroker) -> dict:
    """Получает свежую котировку с механизмом повторных попыток при сбое."""
    needed_fields = ['date', 'high', 'low', 'open', 'close', 'volume']

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            last_data = broker.get_stock_quote(TICKERS)
            # tickers_data = last_data.get(_TIKERS, {})

            if not last_data:
                raise ValueError(f"Брокер вернул пустой ответ")

            return {
                ticker: {k: metrics[k] for k in needed_fields if k in metrics}
                for ticker, metrics in last_data.items()
            }

        except Exception as e:
            logging.warning(f"[Попытка {attempt}/{_MAX_RETRIES}] Ошибка получения данных: {e}")
            if attempt == _MAX_RETRIES:
                logging.error("Все попытки запроса данных исчерпаны.")
                raise e

            # Вычисляем время ожидания перед следующей попыткой (2, 4, 8 секунд...)
            sleep_time = _RETRY_BACKOFF_FACTOR ** attempt
            logging.info(f"Ожидание {sleep_time} сек перед повторным запросом...")
            time_module.sleep(sleep_time)


def save_row_to_csv(ticker: str, row_dict: dict):
    """Сохраняет строку котировок в индивидуальный файл тикера за текущий день."""
    try:
        current_date_str = datetime.now().strftime('%Y%m%d')
        filename = f"quotes_{ticker}_{current_date_str}.csv"
        df_row = pd.DataFrame([row_dict])
        file_exists = os.path.exists(filename)
        df_row.to_csv(filename, mode='a', header=not file_exists, index=False, encoding='utf-8')
    except Exception as e:
        logging.error(f"Не удалось записать CSV для {ticker}: {e}")


def restore_live_rows_from_csv(tickers_list: list) -> dict:
    """
    Проверяет наличие локальных CSV-файлов за сегодняшний день.
    Если они есть, восстанавливает из них накопленные строки в live_rows.
    """
    restored_data = {ticker: [] for ticker in tickers_list}
    current_date_str = datetime.now().strftime('%Y%m%d')

    logging.info("Проверка локальных бэкапов для восстановления данных сессии...")

    for ticker in tickers_list:
        filename = f"quotes_{ticker}_{current_date_str}.csv"
        if os.path.exists(filename):
            try:
                # Читаем файл и преобразуем его обратно в список словарей
                df = pd.read_csv(filename, encoding='utf-8')
                restored_data[ticker] = df.to_dict(orient='records')
                logging.info(f"Успешно восстановлено {len(restored_data[ticker])} строк для {ticker} из {filename}")
            except Exception as e:
                logging.error(f"Ошибка при восстановлении бэкапа для {ticker}: {e}")
        else:
            logging.info(f"Бэкап-файл за сегодня для {ticker} не найден. Старт с чистого листа.")

    return restored_data


def my_target_function():
    """Функция исполнительной логики торгового робота."""
    logging.info("Целевая функция успешно запущена и выполнена.")


if __name__ == "__main__":
    broker = fb.FreedomBroker(FB_EMAIL, FB_PASSWORD)
    strategy = ts.TechnicalStrategy()

    # Загружаем историю один раз при старте
    history_dfs = get_history_data(broker)
    # АВТОМАТИЧЕСКОЕ ВОССТАНОВЛЕНИЕ: Проверяем, работал ли робот сегодня ранее
    live_rows = restore_live_rows_from_csv(TICKERS)

    logging.info("Робот успешно инициализирован и запущен.")
    send_telegram_message("🚀 <b>Торговый робот запущен</b> и начал отслеживание рынка.")

    try:
        while True:
            current_time = datetime.now().time()

            if _START_TIME <= current_time <= _END_TIME:
                logging.info("Время совпадает с интервалом. Получаем данные...")

                try:
                    # 1. Получаем очищенные данные по всем тикерам за один запрос
                    clean_quotes = get_last_row_with_retry(broker)

                    # 2. Обрабатываем каждый тикер отдельно
                    for ticker in TICKERS:
                        current_row = clean_quotes.get(ticker)
                        if not current_row:
                            continue

                        # Сохраняем и добавляем в локальную историю сессии
                        live_rows[ticker].append(current_row)
                        save_row_to_csv(ticker, current_row)
                        # Склеиваем историю с новыми свечами текущего дня для анализа
                        live_df = pd.DataFrame(live_rows[ticker])
                        full_data = pd.concat([history_dfs[ticker], live_df], ignore_index=True)

                        # ПРОВЕРКА МИНИМАЛЬНОГО КОЛИЧЕСТВА СТРОК
                        if len(full_data) < _MIN_ROWS_REQUIRED:
                            logging.warning(
                                f"[{ticker}] Недостаточно данных для анализа. "
                                f"Имеется: {len(full_data)} строк, требуется минимум: {_MIN_ROWS_REQUIRED}. Пропуск."
                            )
                            continue

                        # Расчет стратегии
                        signal, ind = strategy.analyze_market(full_data)

                        # Отправляем сообщение в Telegram только при наличии сигналов BUY или SELL
                        if signal in ["BUY", "SELL"]:
                            my_target_function() # функция покупки или продажи, будет реализованна позже

                            emoji = "🟢" if signal == "BUY" else "🔴"
                            msg_text = (
                                f"{emoji} <b>СИГНАЛ СТРАТЕГИИ: {signal} [{ticker}]</b>\n\n"
                                f"💰 <b>Цена закрытия свечи:</b> <code>{ind['price']:.2f}</code>\n"
                                f"📈 <b>Фильтр EMA 200:</b> {ind['ema_200']:.2f} "
                                f"({'Выше тренда' if ind['price'] > ind['ema_200'] else 'Ниже тренда'})\n\n"
                                f"📊 <b>Индикаторы схождения/импульса:</b>\n"
                                f"• <code>MACD Линия</code>: <b>{ind['macd_line']:.4f}</b>\n"
                                f"• <code>MACD Сигнальная</code>: {ind['macd_signal']:.4f}\n"
                                f"• <code>Stochastic %K</code>: <b>{ind['stoch_k']:.2f}</b>\n\n"
                                f"⏱ <i>Всего свечей в базе робота: {len(full_data)}</i>"
                            )
                            send_telegram_message(msg_text)
                        #logging.info(f"Строк в DataFrame: {len(full_data[ticker])}, сигнал индикатора: {signal}")


                except Exception as loop_error:
                    # Если итерация упала (даже после ретраев), ловим ошибку, чтобы не упал весь скрипт
                    logging.error(f"Итерация пропущена из-за критической ошибки: {loop_error}")
            else:
                logging.info("Текущее время вне интервала. Скрипт в режиме ожидания.")

            # Ожидание следующей итерации (5 минут)
            time_module.sleep(_INTERVAL_SECONDS)

    except KeyboardInterrupt:
        logging.warning("Работа трекера прервана пользователем (Ctrl+C).")
    except Exception as e:
        error_msg = f"❌ <b>Критический сбой скрипта!</b>\nОшибка: {e}"
        logging.critical(f"Критический сбой скрипта: {e}", exc_info=True)
        send_telegram_message(error_msg)
