#ВНИМАНИЕ!!!! данный класс сейчас переделывается, добавлен в проект только для запуска проекта
import requests
import json
import pandas as pd
from datetime import datetime, timedelta

class FreedomBroker:
    __BROKER_URL = 'https://tradernet.com/api/'
    __UTC = "+04:00" # для перевода времени, сервер по ходу в Астане

    def __init__(self, user_name, password):
        self.login = False
        self.answer_text = ''
        self.__auth_data = {}
        self.__user_data = {} #может нужно будет потом
        self.__secret_session_open = False #открыта ли сессия для торговых приказов

        login_param = {
            'login': user_name,
            'password': password,
            'rememberMe': 1,
        }
        try:
            r = requests.post(self.__BROKER_URL +  'check-login-password', login_param)
            # r = requests.post('https://trade.almaty-ffin.kz/api/check-login-password', login_param)
            if r.status_code != 200:
                self.answer_text =  f'Ошибка подключения  к сайту, ошибка: {r.text}'
            else:
                tmp = json.loads(r.text)  # eval(r.text)
                if 'error' in tmp:
                    #если ошибка авторизации
                    self.answer_text = tmp['error']
                    return
                self.__auth_data = {'SID': tmp['SID'], 'userId': tmp['userId']}
                self.login = True
                self.__user_data = tmp
                self.debug_auth_data = tmp
                self.answer_text =  f'авторизация прошла успешно: {r.text}'
        except Exception as e:
            self.answer_text = f'Непредвиденная ошибка при авторизации: {str(e)}'

    def get_history_ticker(self, ticker_, date_from='01.01.2026', date_to='30.01.2026', interval=1440, limit=0):
        """1 параметр - идентификатор ценной бумаги - KZTO.KZ
           2 параметр - стартовая дата в формате ДД.ММ.ГГГГ,
          3 параметр - конечная дата в формате ДД.ММ.ГГГГ,
          4 параметр - интервал в минутах[1, 5, 15, 60, 1440]
          5 параметр - количество свечей"""
        if not self.login:
            self.answer_text = 'Ошибка: Нет активной сессии (вы не авторизованы)'
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
            # Отправляем SID в параметрах запроса (многие методы API Tradernet требуют этого)
            params = {
                'q': json.dumps(r_dict, separators=(',', ':')),
                'sid': self.__auth_data.get('SID')
            }

            r = requests.get(self.__BROKER_URL, params=params, timeout=15)
            if r.status_code != 200:
                self.answer_text = f'Ошибка запроса к API, статус: {r.status_code}'
                return None

            t = r.json()

            # Проверка, вернулись ли данные по тикеру
            if "hloc" not in t or ticker_ not in t["hloc"] or not t["hloc"][ticker_]:
                self.answer_text = f'Данные по тикеру {ticker_} за указанный период отсутствуют'
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

            # Если был указан лимит, возвращаем только последние N строк
            if limit > 0:
                return df.tail(limit).reset_index(drop=True)

            if interval==1440:
                df = df[df['date'] <= formatted_date_to]
            return df

        except Exception as e:
            self.answer_text = f'Ошибка при обработке данных: {str(e)}'
            return None

    def get_tickers_info(self, tikers_list_name=None):
        pass

    def get_user_stock_list(self, list_name='autoTrading'):
        """Возвращает список акций пользователя в виде плоского списка строк.
        list_name (str) - название списка из личного кабинета Tradernet.
                          Если передать 'all', вернутся тикеры из всех списков.
        """
        if not self.login:
            self.answer_text = 'Ошибка: Нет активной сессии (вы не авторизованы)'
            return None

        r_dict = {
            'cmd': 'getUserStockLists',
            'SID': self.__auth_data.get('SID'),  # Безопасное получение SID
        }

        try:
            # Обязательно добавляем params= и timeout=15
            r = requests.get(
                self.__BROKER_URL,
                params={'q': json.dumps(r_dict, separators=(',', ':'))},
                timeout=15
            )

            if r.status_code != 200:
                self.answer_text = f'Ошибка подключения к сайту, статус: {r.status_code}'
                return None

            t = r.json()  # Используем встроенный метод requests вместо json.loads

            # Защита: если ключа нет, вернется пустой список, цикл не упадет
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

                        # Очищаем от возможных дубликатов (если один тикер лежит в разных списках при 'all')
            if list_name == 'all':
                _tickers = list(set(_tickers))

            return _tickers

        except Exception as e:
            self.answer_text = f'Ошибка при получении списков акций: {str(e)}'
            return None

    def get_top_tickers(self, type="stocks", exchange="kazakhstan", gainers=1, limit=20):
        """Возвращает список самых торгуемых бумаг на определенной бирже.

        type(str) - stocks, bonds, futures, funds, indexes
        exchange(str) - kazakhstan, europe, usa, ukraine, currencies
        gainers(int) - 1(Топ быстрорастущих), 0(Топ по объему торгов)
        limit(int) - количество выводимых
        """
        if not self.login:
            self.answer_text = 'Ошибка: Нет активной сессии (вы не авторизованы)'
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
            # Исправлено: явно указываем params= и добавляем sid для прохождения авторизации
            params = {
                'q': json.dumps(r_dict, separators=(',', ':')),
                'sid': self.__auth_data.get('SID')  # Передаем токен сессии
            }

            # Обязательно добавляем timeout=15, чтобы скрипт не зависал вечно
            r = requests.get(self.__BROKER_URL, params=params, timeout=15)

            if r.status_code != 200:
                self.answer_text = f'Ошибка подключения к сайту, статус: {r.status_code}'
                return None

            t = r.json()  # Используем встроенный метод парсинга json

            # Безопасное извлечение ключа 'tickers'. Если его нет, вернет пустой список вместо падения
            return t.get('tickers', [])

        except Exception as e:
            self.answer_text = f'Ошибка при получении топ-тикеров: {str(e)}'
            return None

    def get_balance(self, curr='KZT'):
        """Возвращает сумму на счету по указанной валюте.

        curr (str) - код валюты ('KZT', 'USD', 'EUR', 'RUB' и т.д.)
        Возвращает float (сумму), 0 если валюта не найдена, или None при ошибке сети/API.
        """
        if not self.login:
            self.answer_text = 'Ошибка: Нет active сессии (вы не авторизованы)'
            return None

        r_dict = {
            'cmd': 'getPositionJson',
            'SID': self.__auth_data.get('SID'),
            'params': {}  # Внутри params для этой команды ничего не требуется
        }

        try:
            # Передаем сессию 'sid' на уровне параметров запроса, как требует Tradernet
            params = {
                'q': json.dumps(r_dict, separators=(',', ':')),
            }

            r = requests.get(self.__BROKER_URL, params=params, timeout=10)

            if r.status_code != 200:
                self.answer_text = f'Ошибка подключения к сайту, статус: {r.status_code}'
                return None  # Возвращаем None, чтобы сигнализировать об ошибке сети

            t = r.json()

            # Безопасный переход по ключам ответа брокера
            acc_list = t.get('result', {}).get('ps', {}).get('acc', [])

            # Если список счетов пустой, возможно, у брокера технические работы
            if not acc_list and 'error' in t.get('result', {}):
                self.answer_text = f"Ошибка API: {t['result'].get('error')}"
                return None

            for item in acc_list:
                if item.get('curr') == curr:
                    # Приводим к float, так как баланс — это дробное число
                    return float(item.get('s', 0))

            # Если мы дошли сюда, значит запрос успешный, но конкретно этой валюты на счету нет
            return 0.0

        except Exception as e:
            self.answer_text = f'Ошибка при получении баланса: {str(e)}'
            return None  # Возвращаем None при любых непредвиденных исключениях

    def get_stock_quote(self, tickers):
        """1 параметр - список идентификаторов ценных бумаг - ['KZTO.KZ', 'KEGC.KZ'] (списком!!!!)
            возвращать будем словарь, где ключ это тикер акции, а значения 'high', 'low',
            'open', 'close' - цена последний сделки
        """
        if not self.login:
            return None

        if not isinstance(tickers, list):
            self.answer_text = 'Ошибка: Переданный параметр в функцию не является списком!'
            return None

        r_dict = {
            'cmd': 'getStockQuotesJson',
            'SID': self.__user_data.get('SID'),
            'params': {
                "tickers": tickers
            }
        }

        dict_tickers = {}
        try:
            r = requests.post(self.__BROKER_URL, data={'q': json.dumps(r_dict, separators=(',', ':'))}, timeout=10)
            if r.status_code != 200:
                self.answer_text = f'Ошибка подключения к сайту, ошибка: {r.text}'
                return None

            t = r.json()
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

            return dict_tickers

        except Exception as e:
            self.answer_text = f'Ошибка при получении котировок: {str(e)}'
            return None

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