"""
Telegram + Bevywise CrystalMQ controller for the ESP32 LED.
Install:
    pip install "python-telegram-bot>=21,<23" "paho-mqtt>=2,<3"

Set environment variables:
    TELEGRAM_BOT_TOKEN
    MQTT_HOST
    MQTT_PORT
    MQTT_USERNAME
    MQTT_PASSWORD

Optional:
    MQTT_COMMAND_TOPIC=home/esp32/led/set
    MQTT_STATUS_TOPIC=home/esp32/led/status
    ALLOWED_TELEGRAM_USER_IDS=123456789,987654321
"""

import logging
import os
import threading
import time

import paho.mqtt.client as mqtt
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("esp32-telegram")

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
MQTT_HOST = os.getenv("MQTT_HOST", "crystalmq.bevywise.com")
MQTT_PORT = int(os.getenv("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ["MQTT_USERNAME"]
MQTT_PASSWORD = os.environ["MQTT_PASSWORD"]
COMMAND_TOPIC = os.getenv("MQTT_COMMAND_TOPIC", "home/esp32/led/set")
STATUS_TOPIC = os.getenv("MQTT_STATUS_TOPIC", "home/esp32/led/status")

_allowed_raw = os.getenv("ALLOWED_TELEGRAM_USER_IDS", "").strip()
ALLOWED_IDS = {int(x.strip()) for x in _allowed_raw.split(",") if x.strip()}

mqtt_connected = threading.Event()
last_led_status = {"value": "unknown", "updated": None}


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        log.info("Connected to CrystalMQ")
        mqtt_connected.set()
        client.subscribe(STATUS_TOPIC, qos=0)
    else:
        log.error("MQTT connection failed: %s", reason_code)
        mqtt_connected.clear()


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    mqtt_connected.clear()
    log.warning("Disconnected from MQTT: %s", reason_code)


def on_message(client, userdata, message):
    value = message.payload.decode("utf-8", errors="replace").strip().upper()
    if value in ("ON", "OFF"):
        last_led_status["value"] = value
        last_led_status["updated"] = time.time()
        log.info("LED status received: %s", value)


mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=f"telegram-led-{int(time.time())}",
    protocol=mqtt.MQTTv311,
)
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message


def publish_led(command: str) -> bool:
    if not mqtt_connected.is_set():
        return False
    info = mqtt_client.publish(COMMAND_TOPIC, command, qos=0, retain=False)
    info.wait_for_publish(timeout=5)
    return info.rc == mqtt.MQTT_ERR_SUCCESS and info.is_published()


def authorized(update: Update) -> bool:
    user = update.effective_user
    if not user:
        return False
    # If no allow-list is configured, any Telegram user who finds the bot can use it.
    # Configure ALLOWED_TELEGRAM_USER_IDS for private access.
    return not ALLOWED_IDS or user.id in ALLOWED_IDS


async def deny_if_unauthorized(update: Update) -> bool:
    if not authorized(update):
        await update.effective_message.reply_text("Sorry, you are not authorized to control this device.")
        return True
    return False


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_unauthorized(update):
        return
    await update.effective_message.reply_text(
        "ESP32 LED controller is ready.\n"
        "Commands:\n/on - turn LED on\n/off - turn LED off\n/status - read last reported LED status"
    )


async def led_on(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_unauthorized(update):
        return
    if publish_led("ON"):
        await update.effective_message.reply_text("LED ON command sent.")
    else:
        await update.effective_message.reply_text("MQTT broker is not connected. Please try again shortly.")


async def led_off(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_unauthorized(update):
        return
    if publish_led("OFF"):
        await update.effective_message.reply_text("LED OFF command sent.")
    else:
        await update.effective_message.reply_text("MQTT broker is not connected. Please try again shortly.")


async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await deny_if_unauthorized(update):
        return
    value = last_led_status["value"]
    if value == "unknown":
        await update.effective_message.reply_text(
            "No LED status has been received yet. Make sure the ESP32 firmware publishes "
            f"ON/OFF to MQTT topic: {STATUS_TOPIC}"
        )
    else:
        await update.effective_message.reply_text(f"Last reported LED status: {value}")


async def post_init(application: Application):
    log.info("Connecting to MQTT broker %s:%s", MQTT_HOST, MQTT_PORT)
    mqtt_client.connect(MQTT_HOST, MQTT_PORT, keepalive=45)
    mqtt_client.loop_start()


async def post_shutdown(application: Application):
    mqtt_client.loop_stop()
    mqtt_client.disconnect()


def main():
    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("on", led_on))
    app.add_handler(CommandHandler("off", led_off))
    app.add_handler(CommandHandler("status", status))
    app.run_polling()


if __name__ == "__main__":
    main()
