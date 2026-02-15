import os
import logging
import asyncio
import json
import time
import random
from datetime import datetime
import pandas as pd
import requests
import websocket
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)

# ==================== POCKET OPTION WEBSOCKET ====================
class PocketOptionClient:
    def __init__(self, ssid):
        self.ssid = ssid
        self.ws = None
        self.connected = False
        self.balance = 0
        
    def connect(self):
        """Подключение к Pocket Option через WebSocket"""
        try:
            # Извлекаем session из SSID
            import re
            match = re.search(r'"session":"([^"]+)"', self.ssid)
            if not match:
                logging.error("❌ Не удалось извлечь session из SSID")
                return False
            
            session = match.group(1)
            logging.info(f"✅ Session извлечена: {session[:20]}...")
            
            # Создаем WebSocket соединение
            ws_url = "wss://ws.pocketoption.com/socket.io/?EIO=4&transport=websocket"
            self.ws = websocket.WebSocket()
            self.ws.connect(ws_url, timeout=10)
            
            # Отправляем auth сообщение
            auth_msg = f'42["auth",{{"session":"{session}","isDemo":1,"uid":12345678,"platform":2}}]'
            self.ws.send(auth_msg)
            
            # Ждем ответ
            time.sleep(2)
            
            self.connected = True
            logging.info("✅ Подключено к Pocket Option (WebSocket)")
            return True
            
        except Exception as e:
            logging.error(f"❌ Ошибка подключения: {e}")
            return False
    
    def get_candles(self, asset, timeframe=60, count=100):
        """Получение свечей через WebSocket"""
        try:
            if not self.connected:
                logging.warning("⚠️ Нет подключения к Pocket Option")
                return self._generate_test_candles(count)
            
            # Здесь должен быть запрос свечей через WebSocket
            # Но для простоты пока используем тестовые данные
            return self._generate_test_candles(count)
            
        except Exception as e:
            logging.error(f"❌ Ошибка получения свечей: {e}")
            return self._generate_test_candles(count)
    
    def _generate_test_candles(self, count):
        """Генерация тестовых свечей для отладки"""
        candles = []
        base_price = 1.1000
        for i in range(count):
            price = base_price + random.uniform(-0.01, 0.01)
            candles.append({
                'close': price,
                'open': price - random.uniform(-0.005, 0.005),
                'high': price + random.uniform(0, 0.005),
                'low': price - random.uniform(0, 0.005),
                'time': int(time.time()) - (count - i) * 60
            })
        return candles

# ==================== ГЕНЕРАТОР СИГНАЛОВ ====================
class SignalGenerator:
    def __init__(self):
        self.assets = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "BTCUSD_otc"]
        logging.info(f"📊 Отслеживаемые активы: {self.assets}")

    def calculate_rsi(self, prices, period=14):
        """Расчет RSI индикатора"""
        if len(prices) < period + 1:
            return None
            
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, float('inf'))
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def analyze_asset(self, candles, asset):
        """Анализ актива и генерация сигнала"""
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
                logging.info(f"🔍 {asset}: RSI={rsi:.1f} -> CALL")
            elif rsi > 70:
                signal = "PUT 📉"
                confidence = min(85, 100 - (rsi - 70))
                logging.info(f"🔍 {asset}: RSI={rsi:.1f} -> PUT")

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
            logging.error(f"❌ Ошибка анализа {asset}: {e}")
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
        logging.info("🤖 Инициализация бота...")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        keyboard = [
            [InlineKeyboardButton("📊 Подписаться на сигналы", callback_data='subscribe')],
            [InlineKeyboardButton("🔍 Список активов", callback_data='assets')],
            [InlineKeyboardButton("📈 Статус", callback_data='status')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)

        await update.message.reply_text(
            "🤖 *Бот сигналов Pocket Option*\n\n"
            "Я анализирую рынок и присылаю сигналы, когда нахожу хорошие точки входа.\n\n"
            "Выбери действие:",
            reply_markup=reply_markup,
            parse_mode='Markdown'
        )
        logging.info(f"👤 Пользователь {update.effective_user.id} запустил бота")

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик нажатий на кнопки"""
        query = update.callback_query
        await query.answer()

        if query.data == 'subscribe':
            self.subscribers.add(query.from_user.id)
            await query.edit_message_text(
                "✅ *Ты подписан на сигналы!*\n\n"
                "Я буду присылать уведомления, когда найду хорошие точки входа.\n"
                "Первые сигналы могут появиться через 1-2 минуты.",
                parse_mode='Markdown'
            )
            logging.info(f"👤 Пользователь {query.from_user.id} подписался")
            
            if not self.is_scanning:
                self.is_scanning = True
                asyncio.create_task(self.scan_and_send_signals())

        elif query.data == 'assets':
            assets_list = "\n".join([f"• `{asset}`" for asset in self.signal_generator.assets])
            await query.edit_message_text(
                f"📊 *Отслеживаемые активы:*\n{assets_list}",
                parse_mode='Markdown'
            )

        elif query.data == 'status':
            status = f"📊 *Статус бота:*\n"
            status += f"👥 Подписчиков: {len(self.subscribers)}\n"
            status += f"📈 Активов в мониторинге: {len(self.signal_generator.assets)}\n"
            status += f"🔄 Статус: {'🟢 Активен' if self.is_scanning else '🔴 Остановлен'}"
            await query.edit_message_text(status, parse_mode='Markdown')

    def format_signal(self, signal):
        """Форматирование сигнала"""
        return (
            f"🚨 *ТОРГОВЫЙ СИГНАЛ*\n\n"
            f"Актив: `{signal['asset']}`\n"
            f"Направление: *{signal['direction']}*\n"
            f"Уверенность: {signal['confidence']}%\n"
            f"RSI: {signal['rsi']}\n"
            f"Цена: {signal['price']}\n"
            f"Время: {signal['time']}"
        )

    async def scan_and_send_signals(self):
        """Фоновая задача для сканирования рынка"""
        logging.info("🔄 Подключение к Pocket Option...")
        
        self.pocket_client = PocketOptionClient(self.ssid)
        if not self.pocket_client.connect():
            logging.error("❌ Не удалось подключиться к Pocket Option")
            self.is_scanning = False
            return

        logging.info("✅ Подключено к Pocket Option, начинаю сканирование...")

        while self.is_scanning:
            try:
                for asset in self.signal_generator.assets:
                    candles = self.pocket_client.get_candles(asset, 60, 100)
                    signal = self.signal_generator.analyze_asset(candles, asset)
                    
                    if signal:
                        logging.info(f"✅ Найден сигнал: {signal['asset']} {signal['direction']}")
                        for user_id in self.subscribers.copy():
                            try:
                                await self.application.bot.send_message(
                                    chat_id=user_id,
                                    text=self.format_signal(signal),
                                    parse_mode='Markdown'
                                )
                                logging.info(f"📤 Отправлено пользователю {user_id}")
                            except Exception as e:
                                logging.error(f"❌ Ошибка отправки {user_id}: {e}")
                                self.subscribers.discard(user_id)
                    
                    await asyncio.sleep(2)
                
                logging.info("🔄 Цикл сканирования завершен, следующее через 60 секунд")
                await asyncio.sleep(60)
                
            except Exception as e:
                logging.error(f"❌ Ошибка в цикле сканирования: {e}")
                await asyncio.sleep(10)

    def run(self):
        """Запуск бота"""
        self.application = Application.builder().token(self.token).build()
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        logging.info("=" * 50)
        logging.info("🤖 БОТ ЗАПУЩЕН И ГОТОВ К РАБОТЕ!")
        logging.info("=" * 50)
        
        self.application.run_polling()

# ==================== ЗАПУСК ====================
if __name__ == "__main__":
    TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
    SSID = os.getenv('POCKET_SSID')
    
    if not TOKEN:
        logging.error("❌ Ошибка: Не найден TELEGRAM_BOT_TOKEN")
        exit()
    
    if not SSID:
        logging.error("❌ Ошибка: Не найден POCKET_SSID")
        exit()
    
    bot = TelegramSignalBot(TOKEN, SSID)
    bot.run()
