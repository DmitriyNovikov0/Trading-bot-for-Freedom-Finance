import pandas as pd
import pandas_ta_remake as ta
import logging


class TechnicalStrategy:
    """
    Торговая стратегия на основе 3-х индикаторов:
    1. Скользящая средняя (EMA 200) -> Фильтр глобального тренда
    2. MACD (12, 26, 9) -> Подтверждение разворота тренда (пересечение линий)
    3. Stochastic (14, 3, 3) -> Осциллятор зон перекупленности/перепроданности
    """

    def __init__(self):
        # Изменено на 210, так как для анализа свечи -2 (предпоследней)
        # нам физически нужно иметь хотя бы 200 свечей до нее + запас
        self.min_candles_required = 210

    def analyze_market(self, full_dataframe: pd.DataFrame) -> tuple:
        """
        Принимает готовый DataFrame, рассчитывает индикаторы.
        Возвращает кортеж: (сигнал: str, indicators_dict: dict)
        """
        df = full_dataframe.copy()

        # Базовая защита от пустых данных
        if len(df) < self.min_candles_required:
            logging.warning(f"Недостаточно данных. Нужно минимум {self.min_candles_required}. Сейчас: {len(df)}")
            return "HOLD", {}

        # Гарантируем, что типы данных числовые
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors='coerce')

        try:
            # 2. РАСЧЕТ ИНДИКАТОРОВ через pandas_ta
            df['ema_200'] = ta.ema(df['close'], length=200)

            macd_df = ta.macd(df['close'], fast=12, slow=26, signal=9)
            df = pd.concat([df, macd_df], axis=1)

            stoch_df = ta.stoch(high=df['high'], low=df['low'], close=df['close'], k=14, d=3, smooth_k=3)
            df = pd.concat([df, stoch_df], axis=1)

            # Проверка, что индикаторы рассчитались (не NaN в конце)
            if pd.isna(df['ema_200'].iloc[-2]) or pd.isna(df.get('MACD_12_26_9', pd.Series())).iloc[-2]:
                return "HOLD", {}

        except Exception as ta_err:
            logging.error(f"Ошибка при расчете pandas_ta индикаторов: {ta_err}")
            return "HOLD", {}

        # 3. ПОЛУЧЕНИЕ ПОСЛЕДНИХ ЗНАЧЕНИЙ (по закрытой свече индекс -2)
        current_row = df.iloc[-2]
        prev_row = df.iloc[-3]

        close_price = float(current_row['close'])
        ema_200 = float(current_row['ema_200'])

        # Безопасное извлечение названий колонок (pandas_ta может называть их по-разному)
        macd_k = 'MACD_12_26_9'
        macd_s = 'MACDs_12_26_9'
        stoch_k_name = 'STOCHk_14_3_3'

        macd_line = float(current_row[macd_k])
        macd_signal = float(current_row[macd_s])
        prev_macd_line = float(prev_row[macd_k])
        prev_macd_signal = float(prev_row[macd_s])
        stoch_k = float(current_row[stoch_k_name])

        # Формируем словарь для отправки в Telegram
        indicators_snapshot = {
            "price": close_price,
            "ema_200": ema_200,
            "macd_line": macd_line,
            "macd_signal": macd_signal,
            "stoch_k": stoch_k
        }

        logging.info(
            f"Анализ: Цена={close_price:.2f}, EMA200={ema_200:.2f}, "
            f"MACD={macd_line:.4f}/{macd_signal:.4f}, Stoch_K={stoch_k:.2f}"
        )

        # 4. ЛОГИКА ТОРГОВЫХ СИГНАЛОВ
        trend_up = close_price > ema_200
        macd_cross_up = (prev_macd_line <= prev_macd_signal) and (macd_line > macd_signal)
        stoch_oversold = stoch_k < 25

        if trend_up and macd_cross_up and stoch_oversold:
            return "BUY", indicators_snapshot

        trend_down = close_price < ema_200
        macd_cross_down = (prev_macd_line >= prev_macd_signal) and (macd_line < macd_signal)
        stoch_overbought = stoch_k > 75

        if trend_down and macd_cross_down and stoch_overbought:
            return "SELL", indicators_snapshot

        return "HOLD", indicators_snapshot
