import os
from aiogram import Bot, Dispatcher, types
from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application
from aiohttp import web

TOKEN = os.getenv("BOT_TOKEN")

bot = Bot(token=TOKEN)
dp = Dispatcher()


@dp.message()
async def handle_message(message: types.Message):
    await message.answer(
        "أهلاً بك في بوت البث المباشر للدكتور شاكر العاروري ❤️🔥\n"
        "سيتم إعلامك عند بدء البث إن شاء الله!"
    )


async def on_startup(app):
    webhook_url = app['webhook_url']
    await bot.set_webhook(webhook_url)

async def on_shutdown(app):
    await bot.delete_webhook()
    await bot.session.close()


def main():
    app = web.Application()

    # رابط الويب هو رابط الخدمة + /webhook
    webhook_path = "/webhook"
    app['webhook_url'] = os.getenv("RENDER_EXTERNAL_URL") + webhook_path

    handler = SimpleRequestHandler(dp, bot)
    handler.register(app, path=webhook_path)

    setup_application(app, dp, bot=bot)

    app.on_startup.append(on_startup)
    app.on_shutdown.append(on_shutdown)

    return app


if __name__ == "__main__":
    web.run_app(main(), host="0.0.0.0", port=int(os.getenv("PORT", 10000)))
