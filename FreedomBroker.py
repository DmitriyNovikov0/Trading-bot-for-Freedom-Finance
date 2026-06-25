#ВНИМАНИЕ!!!! данный класс сейчас переделывается, добавлен в проект только для запуска проекта
import requests
import json
import os   # Для работы с папками
import pandas as pd
import time # Для экспоненциальной задержки
from json import dumps as json_dumps
from datetime import datetime, timedelta
from typing import Any
import logging  # Подключили стандартный модуль логирования

class FreedomBroker:
    __BROKER_URL = 'https://tradernet.com/api/'
    __BROKER_URL_V2 = 'https://tradernet.com/freedom24.com/api/'
    __BROKER_WEBSOCKET_URL = 'wss://wss.tradernet.com/freedom24.com'

    __UTC = "+04:00" # для перевода времени, сервер по ходу в Астане

    def __init__(self, user_name, password, logger: logging.Logger | None = None, log_dir: str = "logs"):
        # Сохраняем учетные данные для автоматического re-login
        self.__user_name = user_name
        self.__password = password
        self.log_dir = log_dir
        # Настройка логгера: используем переданный снаружи или создаем дефолтный для этого класса
        self.logger = logger or logging.getLogger(__name__)
        # Настройка отдельного файла для критических ошибок (ERROR и выше)
        self.__create_log_directory()
        self.__setup_critical_file_logger()

        self.login = False
        self.answer_text = ''
        self.__auth_data = {}
        self.__user_data = {}  # может нужно будет потом
        self.__secret_session_open = False  # открыта ли сессия для торговых приказов

        # Первая попытка авторизации
        self._perform_login()

    def __create_log_directory(self):
        """Создает папку для логов, если она еще не создана"""
        try:
            if not os.path.exists(self.log_dir):
                os.makedirs(self.log_dir)
                self.logger.info(f"Создана новая директория для логов: {self.log_dir}")
        except Exception as e:
            print(f"Критическая ошибка при создании папки логов {self.log_dir}: {e}")

    def __setup_critical_file_logger(self):
        """Настройка записи критических ошибок в файл broker_errors.log"""
        try:
            log_path = os.path.join(self.log_dir, 'broker_errors.log')
            file_handler = logging.FileHandler(log_path, encoding='utf-8')
            file_handler.setLevel(logging.ERROR)
            formatter = logging.Formatter('%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s')
            file_handler.setFormatter(formatter)
            self.logger.addHandler(file_handler)
        except Exception as e:
            print(f"Не удалось настроить файловый логгер: {e}")

    def __stringify(self, items: list[Any] | dict[Any, Any]) -> str | None:
        """Внутренний метод для безопасного перевода списков/словарей в компактный JSON-строку"""
        try:
            return json_dumps(items, separators=(',', ':'))
        except (TypeError, ValueError) as e:
            self.logger.error("Не удалось сериализовать параметры в JSON строку", exc_info=True)
            return None

    def _perform_login(self, max_retries: int = 3) -> bool:
        """Внутренний метод для выполнения авторизации"""
        login_param = {
            'login': self.__user_name,
            'password': self.__password,
            'rememberMe': 1,
        }
        for attempt in range(max_retries):
            try:
                r = requests.post(self.__BROKER_URL + 'check-login-password', login_param, timeout=10)
                if r.status_code != 200:
                    self.answer_text = f'Ошибка подключения к сайту, ошибка: {r.text}'
                    self.logger.error(self.answer_text)
                    self.login = False
                else:
                    tmp = json.loads(r.text)
                    if 'error' in tmp:
                        self.answer_text = tmp['error']
                        self.logger.warning(f'Ошибка авторизации (неверные данные): {self.answer_text}')
                        self.login = False
                        return False  # При неверном логине/пароле долбиться в API нет смысла

                    self.__auth_data = {'SID': tmp['SID'], 'userId': tmp['userId']}
                    self.login = True
                    self.__user_data = tmp
                    self.debug_auth_data = tmp
                    self.answer_text = f'авторизация прошла успешно: {r.text}'
                    self.logger.info('Пользователь успешно авторизован в системе.')
                    return True

            except Exception as e:
                self.answer_text = f'Ошибка при попытке авторизации {attempt + 1}/{max_retries}: {str(e)}'
                self.logger.error(self.answer_text, exc_info=True)
                self.login = False

            # Если это не последняя попытка — засыпаем
            if attempt < max_retries - 1:
                sleep_time = 2 ** (attempt + 1)
                self.logger.warning(f"Ожидание {sleep_time} сек. перед следующей попыткой входа...")
                time.sleep(sleep_time)

        return False

    def get_history_ticker(self, ticker_, date_from='01.01.2026', date_to='30.01.2026', interval=1440, limit=0, is_retry=False):
        """1 параметр - идентификатор ценной бумаги - KZTO.KZ
           2 параметр - стартовая дата в формате ДД.ММ.ГГГГ,
          3 параметр - конечная дата в формате ДД.ММ.ГГГГ,
          4 параметр - интервал в минутах[1, 5, 15, 60, 1440]
          5 параметр - количество свечей"""
        if not self.login:
            self.logger.info("Сессия отсутствует. Попытка автоматического re-login перед запросом...")
            if not self._perform_login():
                self.answer_text = 'Ошибка: Нет active сессии и не удалось переавторизоваться'
                return None

        now = datetime.now()
        if limit>0:
            # Словарь коэффициентов для минутного эквивалента свечей с запасом на выходные дни
            interval_multipliers = {1: 480, 5: 96, 15: 32, 60: 8, 1440: 2}
            tmp_interval = interval_multipliers.get(interval, interval * 2)

            # Умножаем на 3, чтобы гарантированно перекрыть выходные дни (когда биржа закрыта)
            date_from = now - timedelta(days=(int(limit / tmp_interval) * 3) + 1)
            date_to = now

        else:
            try:
                formatted_date_to = datetime.strptime(date_to, "%d.%m.%Y")
                # Чтобы захватить весь конечный день, ставим время на конец суток
                date_to = formatted_date_to.replace(hour=23, minute=59, second=59)
                date_from = datetime.strptime(date_from, "%d.%m.%Y")
            except ValueError:
                self.answer_text = 'Ошибка: Неверный формат дат. Используйте ДД.ММ.ГГГГ'
                self.logger.error(self.answer_text)
                return None

        # Форматируем даты строго в ISO формат, который ожидает API Tradernet (YYYY-MM-DD)
        formatted_date_from = str(datetime.strftime(date_from, "%d.%m.%Y"))
        formatted_date_to = str(datetime.strftime(date_to, "%d.%m.%Y"))
        r_dict = {
            'cmd': 'getHloc',
            # 'SID' : aut_data['SID'],
            'params': {
                "userId": self.__user_data['userId'],
                "id": ticker_,  # "FB.US",
                "count": -1,
                "timeframe": interval, #  интервал в минутах[ 1, 5, 15, 60, 1440 ]
                "date_from": formatted_date_from,
                #все работает, не работает на маленьких выборках....
                "date_to": formatted_date_to,
                "intervalMode": 'ClosedRay'
            }
        }

        try:
            # Используем новый безопасный метод __stringify для формирования параметра 'q'
            json_q = self.__stringify(r_dict)
            if json_q is None:
                self.answer_text = 'Ошибка: Сбой подготовки параметров запроса (JSON serialization failed)'
                return None

            params = {
                'q': json_q,
                'sid': self.__auth_data.get('SID')
            }

            self.logger.info(f"Запрос истории по тикеру {ticker_} (интервал: {interval}, limit: {limit})")
            r = requests.get(self.__BROKER_URL, params=params, timeout=15)
            if r.status_code != 200:
                self.answer_text = f'Ошибка запроса к API, статус: {r.status_code}'
                self.logger.error(f"{self.answer_text}. Ответ сервера: {r.text}")
                return None
            # # Отправляем SID в параметрах запроса (многие методы API Tradernet требуют этого)
            # params = {
            #     'q': json.dumps(r_dict, separators=(',', ':')),
            #     'sid': self.__auth_data.get('SID')
            # }
            #
            # r = requests.get(self.__BROKER_URL, params=params, timeout=15)
            # if r.status_code != 200:
            #     self.answer_text = f'Ошибка запроса к API, статус: {r.status_code}'
            #     return None

            t = r.json()

            if isinstance(t, dict) and ('error' in t or t.get('code') == 401 or "auth" in str(t).lower()):
                error_msg = t.get('error', 'Сессия устарела')

                if not is_retry:
                    self.logger.warning(f"Обнаружен сбой сессии ({error_msg}). Попытка автоматического re-login...")
                    if self._perform_login():
                        # Рекурсивный вызов с флагом исчерпания попытки, чтобы избежать бесконечного цикла
                        return self.get_history_ticker(ticker_, date_from.strftime("%d.%m.%Y"),
                                                       date_to.strftime("%d.%m.%Y"), interval, limit, is_retry=True)

                self.answer_text = f'Ошибка API после попытки re-login: {error_msg}'
                self.logger.error(self.answer_text)
                return None

            # Проверка, вернулись ли данные по тикеру
            if "hloc" not in t or ticker_ not in t["hloc"] or not t["hloc"][ticker_]:
                self.answer_text = f'Данные по тикеру {ticker_} за указанный период отсутствуют'
                self.logger.warning(self.answer_text)
                return None

            # Сборка DataFrame
            df = pd.DataFrame(t["hloc"][ticker_], columns=['high', 'low', 'open', 'close'])
            df["volume"] = t["vl"][ticker_]

            # Преобразование меток времени (xSeries хранит UNIX-timestamp)
            timestamps = t['xSeries'][ticker_]
            dt_utc = pd.to_datetime(timestamps, unit="s", utc=True)
            # Переводим в часовой пояс UTC+5 (например, Азия/Алматы)
            dt_tz = dt_utc.tz_convert(self.__UTC)
            df.insert(0, 'date', dt_tz)

            self.answer_text = 'Данные успешно получены'
            self.logger.info(f"Успешно обработано {len(df)} свечей для {ticker_}")

            # Если был указан лимит, возвращаем только последние N строк
            if limit > 0:
                return df.tail(limit).reset_index(drop=True)

            if interval==1440:
                df = df[df['date'] <= formatted_date_to]
            return df

        except Exception as e:
            self.answer_text = f'Ошибка при обработке данных: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)
            return None

    def get_user_stock_list(self, list_name='autoTrading', is_retry=False) -> list[str] | None:
        """Возвращает список акций пользователя в виде плоского списка строк.
        list_name (str) - название списка из личного кабинета Tradernet.
                          Если передать 'all', вернутся тикеры из всех списков.
        """
        if not self.login:
            self.logger.info("Сессия отсутствует. Попытка автоматического re-login перед запросом...")
            if not self._perform_login():  # Выполнит до 3 попыток с задержкой
                self.answer_text = 'Ошибка: Нет активной сессии и не удалось переавторизоваться'
                return None

        r_dict = {
            'cmd': 'getUserStockLists',
            'SID': self.__auth_data.get('SID'),  # Безопасное получение SID
            'params': {
                'userId': self.__user_data.get('userId')
            }
        }

        try:
            json_q = self.__stringify(r_dict)
            if json_q is None:
                self.answer_text = 'Ошибка: Сбой подготовки параметров запроса (JSON serialization failed)'
                return None

            params = {
                'q': json_q,
                'sid': self.__auth_data.get('SID')
            }

            self.logger.info(f"Запрос списка акций пользователя (list_name: {list_name})")
            r = requests.get(self.__BROKER_URL, params=params, timeout=15)

            if r.status_code != 200:
                self.answer_text = f'Ошибка запроса к API, статус: {r.status_code}'
                self.logger.error(f"{self.answer_text}. Ответ сервера: {r.text}")  # Логируется в файл ошибок
                return None

            t = r.json()  # Используем встроенный метод requests вместо json.loads

            # 2. АВТОМАТИЧЕСКИЙ RE-LOGIN ПРИ УСТАРЕВАНИИ SID (Если биржа вернула ошибку авторизации)
            if isinstance(t, dict) and ('error' in t or t.get('code') == 401 or "auth" in str(t).lower()):
                error_msg = t.get('error', 'Сессия устарела')

                if not is_retry:
                    self.logger.warning(f"Обнаружен сбой сессии ({error_msg}). Попытка автоматического re-login...")
                    if self._perform_login():
                        # Повторяем запрос рекурсивно с флагом предохранения
                        return self.get_user_stock_list(list_name=list_name, is_retry=True)

                self.answer_text = f'Ошибка API после попытки re-login: {error_msg}'
                self.logger.error(self.answer_text)  # Запишется в файл broker_errors.log
                return None

            # 3. Обработка успешного ответа
            lists_data = t.get('userStockLists', [])
            _tickers = []

            for item in lists_data:
                # Извлекаем список тикеров, если ключа нет — берем пустой список
                current_tickers = item.get('tickers', [])

                if list_name == 'all':
                    # .extend() добавляет элементы внутрь _tickers, сохраняя список плоским
                    _tickers.extend(current_tickers)
                else:
                    if item.get('name') == list_name:
                        _tickers.extend(current_tickers)
                        # Если нашли конкретный список, можно выйти из цикла раньше
                        break

            # Очищаем от возможных дубликатов (актуально для режима 'all')
            if list_name == 'all':
                _tickers = list(set(_tickers))

            self.answer_text = 'Список акций успешно получен'
            self.logger.info(f"Успешно получено тикеров: {len(_tickers)} из списка '{list_name}'")
            return _tickers

        except Exception as e:
            self.answer_text = f'Ошибка при получении списков акций: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)  # Логирует ошибку и весь Traceback в файл
            return None

    def get_top_tickers(self, type="stocks", exchange="kazakhstan", gainers=1, limit=20, is_retry=False) -> list[dict] | list[str] | None:
        """Возвращает список самых торгуемых или растущих бумаг на определенной бирже.

        type(str) - stocks, bonds, futures, funds, indexes
        exchange(str) - kazakhstan, europe, usa, ukraine, currencies
        gainers(int) - 1(Топ быстрорастущих), 0(Топ по объему торгов)
        limit(int) - количество выводимых элементов
        """
        # 1. Проверяем авторизацию. Если сессии нет — пробуем войти заново
        if not self.login:
            self.logger.info("Сессия отсутствует. Попытка автоматического re-login перед запросом...")
            if not self._perform_login():  # Выполнит до 3 попыток с задержкой
                self.answer_text = 'Ошибка: Нет активной сессии и не удалось переавторизоваться'
                return None

        r_dict = {
            'cmd': 'getTopSecurities',
            'params': {
                "type": type,
                "exchange": exchange,
                "gainers": gainers,
                "limit": limit
            }
        }

        try:
            # Безопасная упаковка параметров через встроенный метод __stringify
            json_q = self.__stringify(r_dict)
            if json_q is None:
                self.answer_text = 'Ошибка: Сбой подготовки параметров запроса (JSON serialization failed)'
                return None

            params = {
                'q': json_q,
                'sid': self.__auth_data.get('SID')  # Передаем токен сессии
            }

            self.logger.info(f"Запрос топ-тикеров ({type}, exchange: {exchange}, gainers: {gainers}, limit: {limit})")
            r = requests.get(self.__BROKER_URL, params=params, timeout=15)

            if r.status_code != 200:
                self.answer_text = f'Ошибка запроса к API, статус: {r.status_code}'
                self.logger.error(f"{self.answer_text}. Ответ сервера: {r.text}")  # Логируется в файл ошибок
                return None

            t = r.json()

            # 2. АВТОМАТИЧЕСКИЙ RE-LOGIN ПРИ УСТАРЕВАНИИ SID
            if isinstance(t, dict) and ('error' in t or t.get('code') == 401 or "auth" in str(t).lower()):
                error_msg = t.get('error', 'Сессия устарела')

                if not is_retry:
                    self.logger.warning(f"Обнаружен сбой сессии ({error_msg}). Попытка автоматического re-login...")
                    if self._perform_login():
                        # Повторяем запрос рекурсивно с защитным флагом от бесконечного цикла
                        return self.get_top_tickers(type=type, exchange=exchange, gainers=gainers, limit=limit,
                                                    is_retry=True)

                self.answer_text = f'Ошибка API после попытки re-login: {error_msg}'
                self.logger.error(self.answer_text)  # Запишется в файл broker_errors.log
                return None

            # 3. Безопасное извлечение данных
            tickers_list = t.get('tickers', [])

            self.answer_text = 'Топ-тикеры успешно получены'
            self.logger.info(f"Успешно получено элементов из топа: {len(tickers_list)}")
            return tickers_list

        except Exception as e:
            self.answer_text = f'Ошибка при получении топ-тикеров: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)  # Логирует ошибку и весь Traceback в файл
            return None

    def get_balance(self, curr='KZT', is_retry=False) -> float | None:
        """Возвращает сумму на счету по указанной валюте.

        curr (str) - код валюты ('KZT', 'USD', 'EUR', 'RUB' и т.д.)
        Возвращает float (сумму), 0.0 если валюта не найдена, или None при ошибке сети/API.
        """
        # 1. Проверяем авторизацию. Если сессии нет — пробуем войти заново
        if not self.login:
            self.logger.info("Сессия отсутствует. Попытка автоматического re-login перед запросом...")
            if not self._perform_login():  # Выполнит до 3 попыток с задержкой
                self.answer_text = 'Ошибка: Нет активной сессии и не удалось переавторизоваться'
                return None

        r_dict = {
            'cmd': 'getPositionJson',
            'SID': self.__auth_data.get('SID'),
            'params': {}  # Внутри params для этой команды ничего не требуется
        }

        try:
            # Безопасная упаковка в JSON через встроенный метод __stringify
            json_q = self.__stringify(r_dict)
            if json_q is None:
                self.answer_text = 'Ошибка: Сбой подготовки параметров запроса (JSON serialization failed)'
                return None

            # Передаем сессию 'sid' на верхнем уровне параметров запроса
            params = {
                'q': json_q,
                # 'sid': self.__auth_data.get('SID')
            }

            self.logger.info(f"Запрос баланса по валюте: {curr}")
            r = requests.get(self.__BROKER_URL, params=params, timeout=10)

            if r.status_code != 200:
                self.answer_text = f'Ошибка запроса к API, статус: {r.status_code}'
                self.logger.error(f"{self.answer_text}. Ответ сервера: {r.text}")  # Логируется в файл ошибок
                return None

            t = r.json()

            is_auth_error = False
            error_msg = 'Сессия устарела'

            if isinstance(t, dict):
                if 'error' in t or t.get('code') == 401 or "auth" in str(t).lower():
                    is_auth_error = True
                    error_msg = t.get('error', error_msg)
                elif 'result' in t and isinstance(t['result'], dict) and 'error' in t['result']:
                    # Проверяем специфичные ошибки авторизации внутри вложенного result
                    res_err = str(t['result'].get('error')).lower()
                    if 'auth' in res_err or 'sid' in res_err or 'session' in res_err:
                        is_auth_error = True
                        error_msg = t['result'].get('error')

            if is_auth_error:
                if not is_retry:
                    self.logger.warning(f"Обнаружен сбой сессии ({error_msg}). Попытка автоматического re-login...")
                    if self._perform_login():
                        # Повторяем запрос рекурсивно с защитным флагом от бесконечного цикла
                        return self.get_balance(curr=curr, is_retry=True)

                self.answer_text = f'Ошибка API после попытки re-login: {error_msg}'
                self.logger.error(self.answer_text)  # Запишется в файл broker_errors.log
                return None

            # Безопасный переход по ключам ответа брокера
            result_data = t.get('result', {})
            acc_list = result_data.get('ps', {}).get('acc', [])

            # Если список счетов пустой, возможно, у брокера технические работы
            if not acc_list and 'error' in t.get('result', {}):
                self.answer_text = f"Ошибка API брокера: {result_data.get('error')}"
                self.logger.error(self.answer_text)  # Запишется в файл логов
                return None

            for item in acc_list:
                if item.get('curr') == curr:
                    # Приводим к float, так как баланс — это дробное число
                    self.answer_text = 'Баланс успешно получен'
                    balance_value = float(item.get('s', 0.0))
                    self.logger.info(f"Текущий баланс в {curr}: {balance_value}")
                    return balance_value

            # Если мы дошли сюда, значит запрос успешный, но конкретно этой валюты на счету нет
            self.answer_text = f'Валюта {curr} не найдена на аккаунте, баланс принят за 0.0'
            self.logger.info(self.answer_text)
            return 0.0

        except Exception as e:
            self.answer_text = f'Ошибка при получении баланса: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)  # Запишет ошибку и весь Traceback в файл логов
            return None

    def get_stock_quote(self, tickers: list[str], is_retry: bool = False) -> dict[str, dict[str, Any]] | None:
        """1 параметр - список идентификаторов ценных бумаг - ['KZTO.KZ', 'KEGC.KZ'] (списком!!!!)
            возвращает словарь, где ключ — это тикер акции, а значение — словарь с параметрами:
            'date', 'high', 'low', 'open', 'close' (цена последней сделки), 'volume' и данные стакана.
        """
        # 1. Проверяем авторизацию. Если сессии нет — пробуем войти заново
        if not self.login:
            self.logger.info("Сессия отсутствует. Попытка автоматического re-login перед запросом...")
            if not self._perform_login():  # Выполнит до 3 попыток с задержкой
                self.answer_text = 'Ошибка: Нет активной сессии и не удалось переавторизоваться'
                return None

        # Проверка типа входных данных
        if not isinstance(tickers, list):
            self.answer_text = 'Ошибка: Переданный параметр в функцию не является списком!'
            self.logger.error(self.answer_text)  # Запишется в файл broker_errors.log
            return None

        if not tickers:
            self.answer_text = 'Ошибка: Переданный список тикеров пуст!'
            self.logger.warning(self.answer_text)
            return {}

        r_dict = {
            'cmd': 'getStockQuotesJson',
            'SID': self.__user_data.get('SID'),
            'params': {
                "tickers": tickers
            }
        }

        dict_tickers = {}
        try:
            # Безопасная упаковка в JSON через встроенный метод __stringify
            json_q = self.__stringify(r_dict)
            if json_q is None:
                self.answer_text = 'Ошибка: Сбой подготовки параметров запроса (JSON serialization failed)'
                return None

            # В Tradernet токен сессии 'sid' должен передаваться на верхнем уровне параметров запроса
            params = {
                'q': json_q,
                'sid': self.__auth_data.get('SID')
            }

            self.logger.info(f"Запрос текущих котировок для списков тикеров: {tickers}")
            r = requests.post(self.__BROKER_URL, data=params, timeout=15)

            if r.status_code != 200:
                self.answer_text = f'Ошибка запроса к API, статус: {r.status_code}'
                self.logger.error(f"{self.answer_text}. Ответ сервера: {r.text}")  # Логируется в файл ошибок
                return None

            t = r.json()

            # 2. АВТОМАТИЧЕСКИЙ RE-LOGIN ПРИ УСТАРЕВАНИИ SID
            is_auth_error = False
            error_msg = 'Сессия устарела'

            if isinstance(t, dict):
                if 'error' in t or t.get('code') == 401 or "auth" in str(t).lower():
                    is_auth_error = True
                    error_msg = t.get('error', error_msg)
                elif 'result' in t and isinstance(t['result'], dict) and 'error' in t['result']:
                    res_err = str(t['result'].get('error')).lower()
                    if 'auth' in res_err or 'sid' in res_err or 'session' in res_err:
                        is_auth_error = True
                        error_msg = t['result'].get('error')

            if is_auth_error:
                if not is_retry:
                    self.logger.warning(f"Обнаружен сбой сессии ({error_msg}). Попытка автоматического re-login...")
                    if self._perform_login():
                        # Повторяем запрос рекурсивно с защитным флагом от бесконечного цикла
                        return self.get_stock_quote(tickers=tickers, is_retry=True)

                self.answer_text = f'Ошибка API после попытки re-login: {error_msg}'
                self.logger.error(self.answer_text)  # Запишется в файл broker_errors.log
                return None

            quotes_list = t.get('result', {}).get('q', [])

            for ticker_data in quotes_list:
                ticker_name = ticker_data.get('c')
                if not ticker_name:
                    continue

                # Безопасно обрабатываем дату
                raw_date = ticker_data.get('ltt', '')
                formatted_date = raw_date.replace("T", " ") if raw_date else ""

                dict_tickers[ticker_name] = {
                    'date': formatted_date,
                    'high': ticker_data.get('maxtp'),
                    'low': ticker_data.get('mintp'),
                    'open': ticker_data.get('op'),
                    'close': ticker_data.get('ltp'),
                    'volume': ticker_data.get('vol'),
                    'bap': ticker_data.get('bap'),  # Лучшая цена продажи (Ask)
                    'bas': ticker_data.get('bas'),  # Объем на продажу
                    'bbp': ticker_data.get('bbp'),  # Лучшая цена покупки (Bid)
                    'bbs': ticker_data.get('bbs'),  # Объем на покупку
                }

            self.answer_text = 'Котировки успешно получены'
            self.logger.info(f"Успешно получены котировки для {len(dict_tickers)} из {len(tickers)} тикеров")
            return dict_tickers

        except Exception as e:
            self.answer_text = f'Ошибка при получении котировок: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)  # Логирует ошибку и весь Traceback в файл логов
            return None

    def get_tickers_info(self, tikers_list_name=None):
        pass

#добавим покупку и продажу, это работает только в API 2 версии
    def buy(self):
        pass

    def sell(self):
        pass


#Далее идут индикаторы
    def get_elder_index(self, data, timeperiod=13):
        """Возвращает список с индекс силы Элдера, при желании можно сконкатенировать с основным датафреймом полученным
        в этом классе ВНИМАНИЕ!!!! volume, close, high, low все с маленькой буквы
        на вход подается датафрейм и TIMEPERIOD - нужен для расчета экспаницеального скользящего среднего"""
        force_index, price_change, out_data = [], [], []
        price_change = data['close'].diff()
        #Рассчитать сырой индекс силы
        force_index = price_change * data['volume']
        #Сгладить индекс с помощью EMA (например, за 13 периодов)
        out_data = force_index.ewm(span=timeperiod, adjust=False).mean()
        return out_data

    def get_NHNL(self, data, timeperiod=10):
        """
        принемат на вход список
        Возвращает NHNL и NHNL сглаженную"""
        out_data, out_data_sma = [], []
        out_data = data['NHNL'] = data['high'] - data['low']
        out_data_sma = out_data.rolling(window=timeperiod).mean()
        return out_data, out_data_sma