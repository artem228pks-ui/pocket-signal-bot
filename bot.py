import os
import sys
import time
import json
import threading
import logging
from datetime import datetime
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Настройка логирования
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ==================== ТВОИ ДАННЫЕ (ВСТАВЬ СВОИ) ====================
TOKEN = "8260184898:AAGSTkqgWvIyAhkAnpO4xscGg7qvFjFdd9g"  # Твой токен
SSID = """42["auth",{"session":"s%3AI6UMmR6CNcOHP0u1Wk3iVqZ2DhMEt7XojHAdmTlTjAcjlB6so9n4q8TpLXQrVfYw","isDemo":1,"uid":87654321,"platform":2}]"""  # Мой SSID

# ==================== POCKET OPTION API ====================
class PocketOptionClient:
    def __init__(self, ssid):
        self.ssid = ssid
        self.api = None
        self.connected = False
        self.balance = 0
        
    def connect(self):
        """Подключение к Pocket Option"""
        try:
            # Попробуем импортировать библиотеку
            try:
                from pocketoptionapi.stable_api import PocketOption
                self.api = PocketOption(self.ssid)
                self.connected, message = self.api.connect()
                
                if self.connected:
                    self.api.change_balance("PRACTICE")
                    self.balance = self.api.get_balance()
                    logging.info(f"✅ Подключено! Баланс: ${self.balance}")
                    return True
                else:
                    logging.error(f"❌ Ошибка: {message}")
                    return False
            except ImportError:
                logging.warning("⚠️ Библиотека не установлена, использую тестовый режим")
                self.connected = True
                return True
                
        except Exception as e:
            logging.error(f"❌ Ошибка: {e}")
            return False
    
    def get_candles(self, asset, timeframe=60, count=100):
        """Получение свечей"""
        try:
            if not self.connected:
                return None
                
            # Если библиотека есть - получаем реальные свечи
            if self.api:
                candles = self.api.get_candles(asset, timeframe, count)
                return candles
            else:
                # Тестовые данные
                import random
                candles = []
                base_price = 1.1000
                for i in range(count):
                    price = base_price + random.uniform(-0.01, 0.01)
                    candles.append({
                        'close': price,
                        'open': price - random.uniform(-0.005, 0.005),
                        'high': price + random.uniform(0, 0.005),
                        'low': price - random.uniform(0, 0.005),
                        'time': i
                    })
                return candles
                
        except Exception as e:
            logging.error(f"❌ Ошибка получения свечей: {e}")
            return None

# ==================== ГЕНЕРАТОР СИГНАЛОВ ====================
class SignalGenerator:
    def __init__(self):
        self.assets = [
            "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", 
            "AUDUSD_otc", "BTCUSD_otc"
        ]
        logging.info(f"📊 Активы: {self.assets}")

    def calculate_rsi(self, prices, period=14):
        """Расчет RSI"""
        if len(prices) < period + 1:
            return None
            
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, float('inf'))
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def analyze_asset(self, candles, asset):
        """Анализ актива"""
        if candles is None or len(candles) < 50:
            return None

        try:
            df = pd.DataFrame(candles)
            prices = df['close']
            
            rsi_series = self.calculate_rsi(prices)
            if rsi_series is None or len(rsi_series) == 0:
                return None
                
            rsi = rsi_series.iloc[-1]
            current_price = prices.iloc[-1]

            signal = None
            confidence = 0

            if rsi < 30:
                signal = "CALL 📈"
                confidence = min(85, 100 - (30 - rsi))
            elif rsi > 70:
                signal = "PUT 📉"
                confidence = min(85, 100 - (rsi - 70))

            if signal and confidence > 60:
                return {
                    'asset': asset,
                    'direction': signal,
                    'confidence': round(confidence, 1),
                    'rsi': round(rsi, 1),
                    'price': round(current_price, 5),
                    'time': datetime.now().strftime('%H:%M:%S')
                }
            return None
            
        except Exception as e:
            logging.error(f"❌ Ошибка анализа: {e}")
            return None

# ==================== TELEGRAM БОТ ====================
class TelegramSignalBot:
    def __init__(self, token, ssid):
        self.token = token
        self.ssid = ssid
        self.pocket_client = None
        self.signal_generator = SignalGenerator()
        self.subscribers = set()
        self.is_scanning = False
        self.scan_thread = None

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Команда /start"""
        keyboard = [
            [InlineKeyboardButton("📊 Подписаться", callback_data='subscribe')],
            [InlineKeyboardButton("🔍 Активы", callback_data='assets')],
            [InlineKeyboardButton("📈 Статус", callback_data='status')],
            [InlineKeyboardButton("🛑 Отписаться", callback_data='unsubscribe')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "🤖 *Бот сигналов Pocket Option*\n\n"
            "Нажми **Подписаться** для получения сигналов",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик кнопок"""
        query = update.callback_query
        await query.answer()

        user_id = query.from_user.id

        if query.data == 'subscribe':
            self.subscribers.add(user_id)
            await query.edit_message_text("✅ Ты подписан на сигналы!")
            
            if not self.is_scanning:
                self.is_scanning = True
                self.scan_thread = threading.Thread(target=self.scan_loop, daemon=True)
                self.scan_thread.start()

        elif query.data == 'assets':
            assets_list = "\n".join([f"• {a}" for a in self.signal_generator.assets])
            await query.edit_message_text(f"📊 *Активы:*\n{assets_list}", parse_mode='Markdown')

        elif query.data == 'status':
            status = f"👥 Подписчиков: {len(self.subscribers)}\n"
            status += f"🔄 Статус: {'Активен' if self.is_scanning else 'Остановлен'}"
            await query.edit_message_text(status)

        elif query.data == 'unsubscribe':
            if user_id in self.subscribers:
                self.subscribers.remove(user_id)
                await query.edit_message_text("🛑 Ты отписался")

    def format_signal(self, signal):
        """Форматирование сигнала"""
        return (
            f"🚨 *СИГНАЛ*\n\n"
            f"Актив: `{signal['asset']}`\n"
            f"Направление: *{signal['direction']}*\n"
            f"Уверенность: {signal['confidence']}%\n"
            f"RSI: {signal['rsi']}\n"
            f"Цена: {signal['price']}\n"
            f"Время: {signal['time']}"
        )

    def scan_loop(self):
        """Цикл сканирования в отдельном потоке"""
        # Подключаемся к Pocket Option
        self.pocket_client = PocketOptionClient(self.ssid)
        if not self.pocket_client.connect():
            logging.error("❌ Не удалось подключиться")
            self.is_scanning = False
            return

        app = Application.builder().token(self.token).build()

        while self.is_scanning:
            try:
                for asset in self.signal_generator.assets:
                    candles = self.pocket_client.get_candles(asset, 60, 100)
                    signal = self.signal_generator.analyze_asset(candles, asset)
                    
                    if signal:
                        for user_id in self.subscribers.copy():
                            try:
                                app.bot.send_message(
                                    chat_id=user_id,
                                    text=self.format_signal(signal),
                                    parse_mode='Markdown'
                                )
                            except:
                                self.subscribers.discard(user_id)
                    
                    time.sleep(2)
                
                time.sleep(60)
                
            except Exception as e:
                logging.error(f"❌ Ошибка: {e}")
                time.sleep(10)

    def run(self):
        """Запуск бота"""
        app = Application.builder().token(self.token).build()
        app.add_handler(CommandHandler("start", self.start))
        app.add_handler(CallbackQueryHandler(self.button_handler))
        
        logging.info("🤖 БОТ ЗАПУЩЕН!")
        app.run_polling()

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    bot = TelegramSignalBot(TOKEN, SSID)
    bot.run()
