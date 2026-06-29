#ВНИМАНИЕ!!!! Версия класса 2, добавлен в проект только что бы код работал, будет переделываться
import requests
from hmac import new as hmac_new
import os   # Для работы с папками
import pandas as pd
import time # Для экспоненциальной задержки
from json import dumps as json_dumps
from datetime import datetime, timedelta
from typing import Any, ClassVar
import logging  # Подключили стандартный модуль логирования

class FreedomBroker:
    __BROKER_URL: ClassVar[str] = 'https://tradernet.com/freedom24.com'
    __BROKER_WEBSOCKET_URL: ClassVar[str] = 'wss://wss.tradernet.com/freedom24.com'
    __UTC: ClassVar[str] = "+04:00" # для перевода времени, сервер по ходу в Астане
    __DURATION_MAP: ClassVar[dict[str, int]] = {
        'day': 1,  # Ордер действует до конца дня
        'ext': 2,  # Ордер действует расширенное время
        'gtc': 3   # Ордер действует до отмены
    }


    def __init__(self, public_key: str = '', private_key: str = '', logger: logging.Logger | None = None, log_dir: str = "logs"):
        #для V2 и V3 нужны ключи, ключи генерируются на сайте https://freedom24.com/tradernet-api/auth-api
        self.__public_key = public_key
        self.__private_key = private_key

        self.log_dir = log_dir
        # Настройка логгера: используем переданный снаружи или создаем дефолтный для этого класса
        self.logger = logger or logging.getLogger(__name__)
        # Настройка отдельного файла для критических ошибок (ERROR и выше)2
        self.__create_log_directory()
        self.__setup_critical_file_logger()
        self.answer_text = ''

        self.login = True
        self.__user_data = {}  # может нужно будет потом


    @staticmethod
    def __sign(
        key: str,
        message: str = '',
        algorithm_name: str = 'sha256'
    ) -> str:
        return hmac_new(
            key=key.encode(),
            msg=message.encode(),
            digestmod=algorithm_name
        ).hexdigest()

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

    def __authorized_request(
            self,
            cmd: str,
            params: dict[str, Any] | None = None,
            version: int = 2
    ) -> Any:
        """
        Отправка форматированного и подписанного запроса к API Tradernet
        с использованием авторизации по HMAC-ключам (V2/V3 через заголовки).
        """
        # Приводим к единому стандарту приватных переменных из __init__
        if not self.__public_key or not self.__private_key:
            self.logger.error('Критическая ошибка: API-ключи (Public/Private) не заданы или пусты')
            raise ValueError('Keypair is not valid')

        headers = {'Content-Type': 'application/json'}
        params = params or {}

        # Корректно выстраиваем базовый домен. Для заголовков X-NtApi базовый URL должен быть https://tradernet.com
        # Очищаем базовый URL от возможных хвостов вроде /api/ или /freedom24.com
        base_domain = "https://tradernet.com"
        url = f"{base_domain}/api/{cmd}"

        self.logger.debug('Making an authorized request to APIv%s: %s', version, url)

        try:
            if version in (2, 3):
                # Сериализуем параметры через ваш безопасный метод __stringify
                payload = self.__stringify(params)
                if payload is None:
                    self.logger.error(f'Ошибка сериализации параметров для команды {cmd}')
                    return None

                # Используем уже импортированный в начале файла модуль time
                timestamp = str(int(time.time()))
                message = payload + timestamp

                # Подписываем тело запроса и упаковываем в заголовки по спецификации Tradernet
                headers['X-NtApi-PublicKey'] = self.__public_key
                headers['X-NtApi-Timestamp'] = timestamp
                headers['X-NtApi-Sig'] = self.__sign(self.__private_key, message)
            else:
                self.logger.error(f'Unsupported API version {version}')
                raise ValueError(f'Unsupported API version {version}')

            self.logger.debug('Sending POST to %s, headers: %s', url, headers)

            # Выполняем POST запрос. Передаем именно data=payload (сырую строку JSON)
            response = requests.post(url, headers=headers, data=payload, timeout=15)

            if response.status_code != 200:
                self.answer_text = f'Ошибка сети при вызове {cmd}, статус: {response.status_code}'
                self.logger.error(f"{self.answer_text}. Ответ сервера: {response.text[:200]}")
                return None

            result = response.json()

            # Проверяем, вернул ли сервер внутреннюю ошибку бизнес-логики Tradernet
            if isinstance(result, dict) and 'errMsg' in result:
                self.answer_text = f"Ошибка API Tradernet ({cmd}): {result['errMsg']}"
                self.logger.error('Error in %s: %s', cmd, result['errMsg'])

            return result

        except Exception as e:
            self.answer_text = f'Критическая ошибка внутри authorized_request ({cmd}): {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)
            return None

    # этот метод выставляет ордера как на покупку так и на продажу!
    def __place_order(
            self,
            symbol: str,
            quantity: int = 1,
            price: float = 0.0,
            duration: str = 'day',
            use_margin: bool = True,
            custom_order_id: int | None = None
    ) -> dict[str, Any] | None:
            """Выставление нового ордера через подпись HMAC ключами."""
            duration = duration.lower()
            if duration == 'ioc':
                order = self.place_order(symbol, quantity, price, 'day', use_margin, custom_order_id)
                if order and isinstance(order, dict) and 'order_id' in order:
                    self.cancel(order['order_id'])
                return order

            if duration not in self.__DURATION_MAP:
                raise ValueError(f'Неизвестная длительность ордера: {duration}')

            if quantity > 0:
                action_id = 2 if use_margin else 1
            elif quantity < 0:
                action_id = 4 if use_margin else 3
            else:
                raise ValueError('Количество бумаг не может быть нулевым!')

            # Перенаправляем всё выполнение в универсальный метод
            return self.__authorized_request(
                'putTradeOrder',
                {
                    'instr_name': symbol,
                    'action_id': action_id,
                    'order_type_id': 2 if price != 0.0 else 1,
                    'qty': abs(quantity),
                    'limit_price': price,
                    'expiration_id': self.__DURATION_MAP[duration],
                    'user_order_id': custom_order_id
                }
            )

    def get_history_ticker(self, ticker_: str, date_from: str = '01.01.2026', date_to: str = '30.01.2026',
                           interval: int = 1440, limit: int = 0) -> pd.DataFrame | None:
        """1 параметр - идентификатор ценной бумаги - KZTO.KZ
           2 параметр - стартовая дата в формате ДД.ММ.ГГГГ,
          3 параметр - конечная дата в формате ДД.ММ.ГГГГ,
          4 параметр - интервал в минутах[1, 5, 15, 60, 1440]
          5 параметр - количество свечей"""

        now = datetime.now()

        if limit>0:
            # Словарь коэффициентов для минутного эквивалента свечей с запасом на выходные дни
            interval_multipliers = {1: 480, 5: 96, 15: 32, 60: 8, 1440: 2}
            tmp_interval = interval_multipliers.get(interval, interval * 2)

            # Умножаем на 3, чтобы гарантированно перекрыть выходные дни (когда биржа закрыта)
            calculated_days = (int(limit / tmp_interval) * 3) + 1
            dt_from = now - timedelta(days=calculated_days)
            dt_to = now

        else:
            try:
                dt_from = datetime.strptime(date_from, "%d.%m.%Y")
                # Захватываем весь конечный день до последней секунды суток
                dt_to = datetime.strptime(date_to, "%d.%m.%Y").replace(hour=23, minute=59, second=59)
            except ValueError:
                self.answer_text = 'Ошибка: Неверный формат дат. Используйте ДД.ММ.ГГГГ'
                self.logger.error(self.answer_text)
                return None

        # Форматируем даты строго в ISO формат, который ожидает API Tradernet (YYYY-MM-DD)
        api_date_from = dt_from.strftime("%Y-%m-%d")
        api_date_to = dt_to.strftime("%Y-%m-%d")

        # Формируем только "params" для передачи в __authorized_request
        api_params = {
            "id": ticker_,
            "count": -1,  # В V2 API -1 означает отдавать всё в рамках указанных дат
            "timeframe": interval,
            "date_from": api_date_from,
            "date_to": api_date_to,
            "intervalMode": 'ClosedRay'
        }

        self.logger.info(
            f"Запрос истории по тикеру {ticker_} (интервал: {interval}, limit: {limit}, {api_date_from} -> {api_date_to})")

        # Выполняем подписанный POST запрос через новый транспортный метод
        t = self.__authorized_request(cmd='getHloc', params=api_params, version=2)

        if not t:
            # Ошибка парсинга или сети уже залогирована внутри __authorized_request
            return None

        # Обработка полученных данных
        try:
            # Проверка, вернулись ли данные по тикеру
            if "hloc" not in t or ticker_ not in t["hloc"] or not t["hloc"][ticker_]:
                self.answer_text = f'Данные по тикеру {ticker_} за период {api_date_from} - {api_date_to} отсутствуют'
                self.logger.warning(self.answer_text)
                return None

            # Сборка DataFrame
            df = pd.DataFrame(t["hloc"][ticker_], columns=['high', 'low', 'open', 'close'])

            if "vl" in t and ticker_ in t["vl"]:
                df["volume"] = t["vl"][ticker_]
            else:
                df["volume"] = 0

            # Преобразование меток времени (Tradernet возвращает UNIX-timestamp в секундах)
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
                df = df[df['date'] <= api_date_to]

            return df.reset_index(drop=True)


        except Exception as e:
            self.answer_text = f'Ошибка при обработке DataFrame для {ticker_}: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)
            return None

    def get_user_stock_list(self, list_name='autoTrading', is_retry=False) -> list[str] | None:
        """Возвращает список акций пользователя в виде плоского списка строк.
        list_name (str) - название списка из личного кабинета Tradernet.
                          Если передать 'all', вернутся тикеры из всех списков.
        """
        api_params = {}

        self.logger.info(f"Запрос списков акций пользователя через HMAC (целевой список: '{list_name}')")

        # Вызываем наш единый транспортный метод
        t = self.__authorized_request(cmd='getUserStockLists', params=api_params, version=2)

        if not t:
            # Ошибка уже залогирована внутри __authorized_request
            return None

        try:
            lists_data = t if isinstance(t, list) else t.get('userStockLists', [])

            if not lists_data:
                self.answer_text = 'Пользовательские списки акций пусты или не найдены'
                self.logger.warning(self.answer_text)
                return []

            _tickers = []


            for item in lists_data:
                # if not isinstance(item, dict):
                #     continue
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

    def get_top_tickers(self, type: str = "stocks", exchange: str = "kazakhstan", gainers: int = 1, limit: int = 20) -> \
            list[dict] | None:
        """
        Возвращает список самых торгуемых или растущих бумаг на определенной бирже через HMAC-авторизацию.
        type: Тип инструментов ('stocks', 'bonds', 'futures', 'funds', 'indexes')
        exchange: Регион/биржа ('kazakhstan', 'europe', 'usa', 'ukraine', 'currencies')
        gainers: 1 (Топ лидеров роста), 0 (Топ по объему торгов)
        limit: Количество возвращаемых элементов
        возвращает : Список словарей с данными лидеров рынка или None в случае ошибки
        """
        if not self.login:
            self.logger.warning("Запрос топ-тикеров отклонен: API-ключи не авторизованы.")
            return None

        # Формируем только внутренний словарь params для команды getTopSecurities
        api_params = {
            "type": type,
            "exchange": exchange,
            "gainers": gainers,
            "limit": limit
        }

        self.logger.info(f"Запрос топ-тикеров (type: {type}, exchange: {exchange}, gainers: {gainers}, limit: {limit})")

        # Отправляем подписанный POST-запрос через наш единый транспортный метод
        t = self.__authorized_request(cmd='getTopSecurities', params=api_params, version=2)

        if not t:
            # Ошибка сети или парсинга уже обработана внутри __authorized_request
            return None

        try:
            # Tradernet API v2 для этого метода возвращает структуру с ключом 'tickers'
            # или массив напрямую. Делаем безопасный перехват обоих вариантов:
            tickers_list = t if isinstance(t, list) else t.get('tickers', [])

            self.answer_text = 'Топ-тикеры успешно получены'
            self.logger.info(f"Успешно получено элементов из топа рынка: {len(tickers_list)}")
            return tickers_list

        except Exception as e:
            self.answer_text = f'Ошибка при обработке списка топ-тикеров: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)
            return None

    def get_balance(self, curr='KZT') -> float | None:
        """Возвращает сумму на счету по указанной валюте.

        curr (str) - код валюты ('KZT', 'USD', 'EUR', 'RUB' и т.д.)
        Возвращает float (сумму), 0.0 если валюта не найдена, или None при ошибке сети/API.
        """
        if not self.login:
            self.logger.warning("Запрос топ-тикеров отклонен: API-ключи не авторизованы.")
            return None

        # Переводим код валюты в верхний регистр для точности сравнения
        curr = curr.upper()
        api_params = {}  # Для getPositionJson дополнительные параметры не требуются

        self.logger.info(f"Запрос баланса по валюте: {curr} через HMAC")

        # Вызываем единый транспортный метод
        t = self.__authorized_request(cmd='getPositionJson', params=api_params, version=2)

        if not t:
            # Ошибка сети или подписи уже залогирована внутри __authorized_request
            return None

        try:
            # В REST API v2 структура ответа упрощена:
            # Данные могут лежать либо в корне ответа (t.get('ps')), либо внутри контейнера t.get('result', {}).get('ps')
            result_data = t if 'ps' in t else t.get('result', {})
            ps_data = result_data.get('ps', {})
            acc_list = ps_data.get('acc', [])

            if not acc_list:
                self.answer_text = 'Список торговых счетов (acc) пуст или не получен от API'
                self.logger.warning(self.answer_text)
                return None

            # Проходим по списку доступных валютных субсчетов
            for item in acc_list:
                if not isinstance(item, dict):
                    continue

                if item.get('curr') == curr:
                    # 's' — это свободные средства (free cash) в данной валюте
                    # Если нужно задействовать общий баланс с учетом ГО/блокировок, используется ключ 'v'
                    balance_value = float(item.get('s', 0.0))

                    self.answer_text = f'Баланс {curr} успешно получен'
                    self.logger.info(f"Текущий свободный баланс в {curr}: {balance_value}")
                    return balance_value

            # Если запрос прошел успешно, но записи о валюте нет — значит средств в ней 0.0
            self.answer_text = f'Валютный счет {curr} не найден на аккаунте, баланс принят за 0.0'
            self.logger.info(self.answer_text)
            return 0.0

        except Exception as e:
            self.answer_text = f'Ошибка при парсинге баланса {curr}: {str(e)}'
            self.logger.error(self.answer_text, exc_info=True)
            return None

    def get_stock_quote(self, tickers: list[str]) -> dict[str, dict[str, Any]] | None:
        """
        Запрашивает текущие котировки и срез стакана (Bid/Ask) по списку ценных бумаг.

        :param tickers: Список идентификаторов ценных бумаг (например, ['KZTO.KZ', 'KEGC.KZ'])
        :return: Словарь, где ключ — тикер, значение — словарь с параметрами, или None при ошибке
        """
        if not self.login:
            self.logger.warning("Запрос топ-тикеров отклонен: API-ключи не авторизованы.")
            return None

        # Проверка типов входных данных
        if not isinstance(tickers, list):
            self.answer_text = 'Ошибка: Переданный параметр не является списком!'
            self.logger.error(self.answer_text)
            return None

        if not tickers:
            self.answer_text = 'Ошибка: Переданный список тикеров пуст!'
            self.logger.warning(self.answer_text)
            return {}

        # Подготовка параметров для метода getStockQuotesJson согласно API V2
        api_params = {
            "tickers": tickers
        }

        self.logger.info(f"Запрос текущих котировок для тикеров: {tickers} через HMAC")

        # Вызываем единый транспортный метод
        t = self.__authorized_request(cmd='getStockQuotesJson', params=api_params, version=2)

        if not t:
            # Сетевая ошибка или ошибка подписи уже обработана в __authorized_request
            return None

        dict_tickers = {}
        try:
            # В REST API v2 блок 'result' отсутствует, структура 'q' лежит прямо в корне ответа t
            # Поддерживаем оба формата для обеспечения максимальной отказоустойчивости
            quotes_list = t.get('q', []) if isinstance(t, dict) else []
            if not quotes_list and isinstance(t, dict) and 'result' in t:
                quotes_list = t.get('result', {}).get('q', [])

            for ticker_data in quotes_list:
                # if not isinstance(ticker_data, dict):
                #     continue
                ticker_name = ticker_data.get('c')
                if not ticker_name:
                    continue

                # Безопасно обрабатываем дату
                raw_date = ticker_data.get('ltt', '')
                formatted_date = raw_date.replace("T", " ") if raw_date else ""

                dict_tickers[ticker_name] = {
                    'date': formatted_date,
                    'high': ticker_data.get('maxtp'),  # Максимальная цена за день
                    'low': ticker_data.get('mintp'),  # Минимальная цена за день
                    'open': ticker_data.get('op'),  # Цена открытия сессии
                    'close': ticker_data.get('ltp'),  # Цена последней сделки (текущая цена)
                    'volume': ticker_data.get('vol'),  # Объем торгов в штуках
                    'bap': ticker_data.get('bap'),  # Лучшая цена продажи (Best Ask Price)
                    'bas': ticker_data.get('bas'),  # Объем на продажу на лучшем уровне Ask
                    'bbp': ticker_data.get('bbp'),  # Лучшая цена покупки (Best Bid Price)
                    'bbs': ticker_data.get('bbs'),  # Объем на покупку на лучшем уровне Bid
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

    def cancel(self, order_id: int) -> dict[str, Any] | None:
        """Отмена ордера через подпись HMAC ключами."""
        return self.__authorized_request('delTradeOrder', {'order_id': order_id})

#добавим покупку и продажу, это работает только в API 2 версии
    def buy(self, symbol: str, quantity: int = 1, price: float = 0.0, duration: str = 'day', use_margin: bool = True,
            custom_order_id: int | None = None) -> dict[str, Any] | None:
        if quantity < 1:
            self.answer_text = 'Ошибка: число покупаемых акций меньше 1!'
            return None

        if duration == 'ioc':
            order = self.trade(
                symbol,
                quantity,
                price,
                'day',
                use_margin,
                custom_order_id
            )
            if 'order_id' in order:
                self.cancel(order['order_id'])
            return order
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