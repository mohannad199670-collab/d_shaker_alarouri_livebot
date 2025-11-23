import telebot
import os
from flask import Flask

BOT_TOKEN = os.getenv("BOT_TOKEN")

bot = telebot.TeleBot(BOT_TOKEN)

app = Flask(__name__)

@app.route('/')
def home():
    return "Bot is running!"

@bot.message_handler(commands=['start'])
def start(message):
    bot.reply_to(message, "أهلاً بك في بوت الدكتور شاكر العاروري ❤️")

@bot.message_handler(func=lambda m: True)
def echo(message):
    bot.reply_to(message, "تم الاستلام 👌")

# تشغيل التليجرام
def bot_run():
    bot.infinity_polling()

if __name__ == "__main__":
    bot_run()
