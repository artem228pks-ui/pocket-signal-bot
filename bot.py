import os
import logging
import asyncio
from datetime import datetime
import pandas as pd
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes
import requests
import json
import time
import hashlib
import websocket
import threading

logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO)

class PocketOptionClient:
    def __init__(self, ssid):
        self.ssid = ssid
        self.api = PocketOption(ssid)
        self.connected = False

    def connect(self):
        try:
            self.connected = self.api.connect()
            if self.connected:
                balance = self.api.get_balance()
                print(f"✅ Подключено! Баланс: ${balance:.2f}")
                return True
            return False
        except Exception as e:
            print(f"Ошибка: {e}")
            return False

    def get_candles(self, asset, timeframe=60, count=100):
        try:
            return self.api.get_candles(asset, timeframe, count)
        except Exception as e:
            print(f"Ошибка: {e}")
            return None

class SignalGenerator:
    def __init__(self):
        self.assets = ["EURUSD_otc", "GBPUSD_otc", "USDJPY_otc", "BTCUSD_otc"]

    def calculate_rsi(self, prices, period=14):
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / loss.replace(0, float('inf'))
        rsi = 100 - (100 / (1 + rs))
        return rsi

    def analyze_asset(self, candles, asset):
        if candles is None or len(candles) < 50:
            return None
        df = pd.DataFrame(candles)
        prices = df['close']
        rsi = self.calculate_rsi(prices).iloc[-1]
        price = prices.iloc[-1]
        if rsi < 30:
            return {'asset': asset, 'direction': 'CALL', 'confidence': 70, 'rsi': round(rsi,1), 'price': round(price,5)}
        elif rsi > 70:
            return {'asset': asset, 'direction': 'PUT', 'confidence': 70, 'rsi': round(rsi,1), 'price': round(price,5)}
        return None

class TelegramSignalBot:
    def __init__(self, token, ssid):
        self.token = token
        self.ssid = ssid
        self.pocket_client = None
        self.signal_generator = SignalGenerator()
        self.subscribers = set()
        self.is_scanning = False

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        keyboard = [[InlineKeyboardButton("📊 Подписаться", callback_data='subscribe')]]
        await update.message.reply_text("Бот сигналов Pocket Option", reply_markup=InlineKeyboardMarkup(keyboard))

    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        if query.data == 'subscribe':
            self.subscribers.add(query.from_user.id)
            await query.edit_message_text("✅ Подписан на сигналы!")
            if not self.is_scanning:
                self.is_scanning = True
                asyncio.create_task(self.scan_and_send_signals())

    async def scan_and_send_signals(self):
        self.pocket_client = PocketOptionClient(self.ssid)
        if not self.pocket_client.connect():
            self.is_scanning = False
            return
        while self.is_scanning:
            try:
                for asset in self.signal_generator.assets:
                    candles = self.pocket_client.get_candles(asset, 60, 100)
                    signal = self.signal_generator.analyze_asset(candles, asset)
                    if signal:
                        for uid in self.subscribers.copy():
                            try:
                                await self.application.bot.send_message(uid, f"📊 {signal['asset']} {signal['direction']} ({signal['confidence']}%)")
                            except:
                                self.subscribers.discard(uid)
                    await asyncio.sleep(2)
                await asyncio.sleep(60)
            except:
                await asyncio.sleep(10)

    def run(self):
        self.application = Application.builder().token(self.token).build()
        self.application.add_handler(CommandHandler("start", self.start))
        self.application.add_handler(CallbackQueryHandler(self.button_handler))
        print("Бот запущен!")
        self.application.run_polling()

if __name__ == "__main__":
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    ssid = os.getenv('POCKET_SSID')
    if not token or not ssid:
        print("Ошибка: Нет токенов!")
        exit()
    bot = TelegramSignalBot(token, ssid)
    bot.run()
