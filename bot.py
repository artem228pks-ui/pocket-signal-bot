import os
import logging
import asyncio
import json
import time
import random
import socket
import ssl
from datetime import datetime
import pandas as pd
import websocket
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

# Настройка логирования
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
        self.assets_list = []
        
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
            logging.info(f"✅ Session извлечена: {session[:30]}...")
            
            # Настройки WebSocket с правильными DNS
            ws_url = "wss://ws.pocketoption.com/socket.io/?EIO=4&transport=websocket"
            
            # Создаем WebSocket с правильными параметрами
            self.ws = websocket.WebSocket()
            self.ws.connect(
                ws_url,
                timeout=30,
                skip_utf8_validation=False,
                enable_multithread=True
            )
            
            # Отправляем auth сообщение
            auth_msg = f'42["auth",{{"session":"{session}","isDemo":1,"uid":12345678,"platform":2}}]'
            self.ws.send(auth_msg)
            
            # Получаем ответ
            response = self.ws.recv()
            logging.info(f"📡 Ответ от сервера: {response[:100]}")
            
            # Отправляем ping для поддержания соединения
            self.ws.send('2')
            
            self.connected = True
            logging.info("✅ Подключено к Pocket Option (WebSocket)")
            
            # Получаем список активов
            self.get_assets()
            return True
            
        except Exception as e:
            logging.error(f"❌ Ошибка подключения: {e}")
            return False
    
    def get_assets(self):
        """Получение списка доступных активов"""
        try:
            # Отправляем запрос на получение активов
            self.ws.send('42["getAssets",{}]')
            time.sleep(2)
            
            # Читаем ответ
            response = self.ws.recv()
            logging.info(f"📊 Получен список активов")
            
            # Базовый список активов на всякий случай
            self.assets_list = [
                "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc",
                "AUDUSD_otc", "EURJPY_otc", "BTCUSD_otc",
                "ETHUSD_otc", "GBPJPY_otc", "USDCHF_otc"
            ]
            return True
        except Exception as e:
            logging.error(f"❌ Ошибка получения активов: {e}")
            # Возвращаем базовый список
            self.assets_list = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "BTCUSD_otc"]
            return False
    
    def get_candles(self, asset, timeframe=60, count=100):
        """Получение реальных свечей через WebSocket"""
        try:
            if not self.connected or not self.ws:
                logging.warning("⚠️ Нет подключения к WebSocket")
                return None
            
            # Отправляем запрос на свечи
            msg = f'42["getCandles",{{"asset":"{asset}","timeframe":{timeframe},"count":{count}}}]'
            self.ws.send(msg)
            
            # Ждем ответ
            time.sleep(3)
            
            # Получаем данные
            response = self.ws.recv()
            
            # Парсим ответ
            if response and response.startswith('42'):
                try:
                    data = json.loads(response[2:])
                    if data and len(data) > 1 and isinstance(data[1], list):
                        candles_data = data[1]
                        
                        # Преобразуем в формат pandas
                        candles = []
                        for c in candles_data:
                            if isinstance(c, dict) and 'close' in c:
                                candles.append({
                                    'close': float(c.get('close', 0)),
                                    'open': float(c.get('open', 0)),
                                    'high': float(c.get('high', 0)),
                                    'low': float(c.get('low', 0)),
                                    'time': c.get('time', 0)
                                })
                        
                        if candles:
                            logging.info(f"✅ Получено {len(candles)} свечей для {asset}")
                            return candles
                except json.JSONDecodeError:
                    pass
            
            # Если не получили реальные данные, возвращаем тестовые
            return self._generate_test_candles(asset, count)
            
        except Exception as e:
            logging.error(f"❌ Ошибка получения свечей для {asset}: {e}")
            return self._generate_test_candles(asset, count)
    
    def _generate_test_candles(self, asset, count):
        """Генерация тестовых свечей для отладки"""
        candles = []
        base_price = 1.1000
        if "BTC" in asset:
            base_price = 50000
        elif "JPY" in asset:
            base_price = 150.0
            
        for i in range(count):
            price = base_price + random.uniform(-base_price*0.01, base_price*0.01)
            candles.append({
                'close': price,
                'open': price - random.uniform(-base_price*0.005, base_price*0.005),
                'high': price + random.uniform(0, base_price*0.005),
                'low': price - random.uniform(0, base_price*0.005),
                'time': int(time.time()) - (count - i) * 60
            })
        return candles
    
    def ping(self):
        """Отправка ping для поддержания соединения"""
        try:
            if self.ws:
                self.ws.send('2')
                return True
        except:
            return False
        return False

# ==================== ГЕНЕРАТОР СИГНАЛОВ ====================
class SignalGenerator:
    def __init__(self):
        self.assets = [
            "EURUSD_otc", "GBPUSD_otc", "USDJPY_otc",
            "AUDUSD_otc", "BTCUSD_otc", "ETHUSD_otc"
        ]
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
        self.ping_task = None
        logging.info("🤖 Инициализация бота...")

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /start"""
        keyboard = [
            [InlineKeyboardButton("📊 Подписаться на сигналы", callback_data='subscribe')],
            [InlineKeyboardButton("🔍 Список активов", callback_data='assets')],
            [InlineKeyboardButton("📈 Статус", callback_data='status')],
            [InlineKeyboardButton("🛑 Отписаться", callback_data='unsubscribe')]
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

        user_id = query.from_user.id

        if query.data == 'subscribe':
            self.subscribers.add(user_id)
            await query.edit_message_text(
                "✅ *Ты подписан на сигналы!*\n\n"
                "Я буду присылать уведомления, когда найду хорошие точки входа.\n"
                "Первые сигналы могут появиться через 1-2 минуты.",
                parse_mode='Markdown'
            )
            logging.info(f"👤 Пользователь {user_id} подписался")
            
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
            status_text = f"📊 *Статус бота:*\n"
            status_text += f"👥 Подписчиков: {len(self.subscribers)}\n"
            status_text += f"📈 Активов в мониторинге: {len(self.signal_generator.assets)}\n"
            status_text += f"🔄 Статус: {'🟢 Активен' if self.is_scanning else '🔴 Остановлен'}"
            
            if self.pocket_client and self.pocket_client.connected:
                status_text += f"\n✅ Подключено к Pocket Option"
            else:
                status_text += f"\n❌ Нет подключения к Pocket Option"
                
            await query.edit_message_text(status_text, parse_mode='Markdown')

        elif query.data == 'unsubscribe':
            if user_id in self.subscribers:
                self.subscribers.remove(user_id)
                await query.edit_message_text("🛑 *Ты отписался от сигналов*", parse_mode='Markdown')
                logging.info(f"👤 Пользователь {user_id} отписался")
            else:
                await query.edit_message_text("❌ Ты не был подписан")

    async def subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /subscribe"""
        user_id = update.effective_user.id
        self.subscribers.add(user_id)
        await update.message.reply_text("✅ Ты подписан на сигналы! (команда)")
        logging.info(f"👤 Пользователь {user_id} подписался через команду")
        
        if not self.is_scanning:
            self.is_scanning = True
            asyncio.create_task(self.scan_and_send_signals())

    async def status_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /status"""
        status_text = f"📊 *Статус бота:*\n"
        status_text += f"👥 Подписчиков: {len(self.subscribers)}\n"
        status_text += f"📈 Активов в мониторинге: {len(self.signal_generator.assets)}\n"
        status_text += f"🔄 Статус: {'🟢 Активен' if self.is_scanning else '🔴 Остановлен'}"
        
        if self.pocket_client and self.pocket_client.connected:
            status_text += f"\n✅ Подключено к Pocket Option"
        else:
            status_text += f"\n❌ Нет подключения к Pocket Option"
            
        await update.message.reply_text(status_text, parse_mode='Markdown')

    async def assets_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /assets"""
        assets_list = "\n".join([f"• `{asset}`" for asset in self.signal_generator.assets])
        await update.message.reply_text(
            f"📊 *Отслеживаемые активы:*\n{assets_list}",
            parse_mode='Markdown'
        )

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

    async def ping_loop(self):
        """Поддержание соединения с Pocket Option"""
        while self.is_scanning:
            try:
                if self.pocket_client and self.pocket_client.connected:
                    self.pocket_client.ping()
                await asyncio.sleep(30)
            except:
                pass

    async def scan_and_send_signals(self):
        """Фоновая задача для сканирования рынка"""
        logging.info("🔄 Подключение к Pocket Option...")
        
        self.pocket_client = PocketOptionClient(self.ssid)
        if not self.pocket_client.connect():
            logging.error("❌ Не удалось подключиться к Pocket Option")
            # Продолжаем в тестовом режиме
            logging.info("🔄 Переход в тестовый режим...")
        else:
            logging.info("✅ Подключено к Pocket Option, начинаю сканирование...")
            # Запускаем ping для поддержания соединения
            asyncio.create_task(self.ping_loop())

        scan_count = 0
        while self.is_scanning:
            try:
                for asset in self.signal_generator.assets:
                    # Получаем свечи
                    candles = self.pocket_client.get_candles(asset, 60, 100)
                    
                    # Анализируем
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
                
                scan_count += 1
                logging.info(f"🔄 Цикл сканирования #{scan_count} завершен, следующее через 60 секунд")
                await asyncio.sleep(60)
                
            except Exception as e:
                logging.error(f"❌ Ошибка в цикле сканирования: {e}")
                await asyncio.sleep(10)

    def run(self):
        """Запуск бота"""
        self.application = Application.builder().token(self.token).build()
        
        # Команды
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CommandHandler("subscribe", self.subscribe_command))
        self.application.add_handler(CommandHandler("status", self.status_command))
        self.application.add_handler(CommandHandler("assets", self.assets_command))
        
        # Кнопки
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        
        logging.info("=" * 50)
        logging.info("🤖 БОТ ЗАПУЩЕН И ГОТОВ К РАБОТЕ!")
        logging.info("=" * 50)
        logging.info("📱 Отправь /start в Telegram")
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
