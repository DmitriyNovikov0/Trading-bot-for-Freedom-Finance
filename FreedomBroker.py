#ВНИМАНИЕ!!!! данный класс сейчас переделывается, добавлен в проект только для запуска проекта
import requests
import json
import pandas as pd
from datetime import datetime, timedelta, datetime

class FreedomBroker:
    __BROKER_URL = 'https://tradernet.com/api/'
    __auth_data = {}
    __UTC = "+04:00" # для перевода времени, сервер по ходу в Астане

    def __init__(self, user_name, password):
        self.login = False
        self.answer_text = ''
        # self.__auth_data = {}
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
          5 параметр - количество дней от текущей даты(если нужно), если указываем количество дней даты не вводятся"""
        if not self.login:
            self.answer_text = 'Ошибка: Нет активной сессии (вы не авторизованы)'
            return None

        now = datetime.now()
        if limit>0:
            # date_list = [now - timedelta(minutes=interval * i) for i in range(200)]
            # date_from = date_list[-1] if date_list else now
            tmp_interval = 0
            if interval == 1:
                tmp_interval = interval * 480
            elif interval == 5:
                tmp_interval = interval * 96
            elif interval == 15:
                tmp_interval = interval * 32
            elif interval == 60:
                tmp_interval = interval * 8
            elif interval == 1440:
                tmp_interval = interval * 2

            date_tmp = now - timedelta(minutes = tmp_interval * limit)
            formatted_date_from = date_tmp.strftime("%d.%m.%Y")
            formatted_date_to = now.strftime("%d.%m.%Y")

            #ПРОВЕРИТЬ ЭТОТ КОД
            # days_needed = max(2, int((interval * limit) / 1440) * 3)
            # date_from_dt = now - timedelta(days=days_needed)
            # date_to_dt = now

        else:
            try:
                formatted_date_from = datetime.strptime(date_from, "%d.%m.%Y")
                formatted_date_to = datetime.strptime(date_to, "%d.%m.%Y")
                # Чтобы захватить весь конечный день, ставим время на конец суток
                formatted_date_to = formatted_date_to.replace(hour=23, minute=59, second=59)
            except ValueError:
                self.answer_text = 'Ошибка: Неверный формат дат. Используйте ДД.ММ.ГГГГ'
                return None


        # Форматируем даты строго в ISO формат, который ожидает API Tradernet (YYYY-MM-DD)
        # formatted_date_from = str(datetime.strptime(date_from, "%d.%m.%Y"))
        # formatted_date_to = str(datetime.strptime(date_to, "%d.%m.%Y"))
        r_dict = {
            'cmd': 'getHloc',
            # 'SID' : aut_data['SID'],
            'params': {
                "userId": self.__user_data['userId'],
                "id": ticker_,  # "FB.US",
                "count": -1,
                "timeframe": interval, #  интервал в минутах[ 1, 5, 15, 60, 1440 ]
                "date_from": str(formatted_date_from),
                #все работает, не работает на маленьких выборках....
                "date_to": str(formatted_date_to),
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

            return df

        except Exception as e:
            self.answer_text = f'Ошибка при обработке данных: {str(e)}'
            return None

    def get_tickers_info(self, tikers_list_name=None):
        pass

    def get_user_stock_list(self, list_name='autoTrading'):
        """
        list_name - название списка из которого будем дергать тикеты,
        возвращает список акций пользователя в виде списка
        :return:

        """
        if not self.login:
            return None
        r_dict = {
            'cmd': 'getUserStockLists',
            'SID': self.__user_data['SID'],
        }

        r = requests.get(self.__BROKER_URL, {'q': json.dumps(r_dict, separators=(',', ':'))})
        if r.status_code != 200:
            self.answer_text = f'Ошибка подключения  к сайту, ошибка: {r.text}'
            return None
        _tickers = []
        t = json.loads(r.text)
        for tickers in t['userStockLists']:
            if list_name == 'all':
                _tickers.append(tickers['tickers'])
            else:
                if tickers['name'] == list_name:
                    _tickers += tickers['tickers']

        return _tickers

    def get_top_tickets(self, type = "stocks", exchange="kazakhstan", gainers=1, limit=20):
        """возвращаем список самых торгуемых бумаг на определенной бирже
        type(str) - stocks(Акции), bonds(Облигации), futures(Фьючерсы), funds(Фонды), indexes(Индексы)
        exchange(str) - kazakhstan(Казахстан), europe(Европа), usa(Америка), ukraine(Украина), currencies(Валюта)
        gainers(int) - 1(Топ быстрорастущих), 0(Топ по объему торгов)
        limit(int) - количество выводимых

        """
        if not self.login:
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

        r = requests.get(self.__BROKER_URL, {'q': json.dumps(r_dict, separators=(',', ':'))})
        if r.status_code != 200:
            self.answer_text = f'Ошибка подключения  к сайту, ошибка: {r.text}'
            return None
        _tickers = [] # честно не помню нахрена объявлял....
        t = json.loads(r.text)

        return t['tickers']

    def get_ballance(self, curr='KZT'):
        """возвращаем сумму на счету
        curr(str) - сумму какой валюты вернуть ('KZT', 'RUR', 'USD')

        """
        if not self.login:
            return None
        r_dict = {
            'cmd': 'getPositionJson',
            'SID': self.__user_data.get('SID'),
            'params': {
            }
        }

        try:
            r = requests.get(self.__BROKER_URL, params={'q': json.dumps(r_dict, separators=(',', ':'))}, timeout=10)
            if r.status_code != 200:
                self.answer_text = f'Ошибка подключения к сайту, ошибка: {r.text}'
                return 0

            t = r.json()
            # Безопасный переход по ключам. Если ключей нет — вернет пустой список
            tmp = t.get('result', {}).get('ps', {}).get('acc', [])

            summ = 0
            for item in tmp:
                if item.get('curr') == curr:
                    summ = item.get('s', 0)
                    break  # Валюта найдена, можно выходить из цикла

            return summ
        except Exception as e:
            self.answer_text = f'Ошибка при получении баланса: {str(e)}'
            return 0

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
            # Заменили хардкод строки на переменную self.__BROKER_URL
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