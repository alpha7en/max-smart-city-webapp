#!/usr/bin/env python3
"""
Скрипт проверки работы бота в мессенджере MAX.
Использует Long Polling (GET /updates) и отправку сообщений (POST /messages).
Токен считывается из token.env или .env автоматически.
"""

import os
import ssl
import json
import time
import urllib.request
import urllib.parse

def get_token():
    for fpath in ['token.env', '.env']:
        if os.path.exists(fpath):
            with open(fpath, 'r', encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue
                    token = line.split('=', 1)[1].strip().strip('\"\'') if '=' in line else line.strip().strip('\"\'')
                    if token:
                        return token
    raise ValueError("Файл с токеном (token.env или .env) не найден!")

TOKEN = get_token()
BASE_URL = "https://platform-api2.max.ru"
CTX = ssl._create_unverified_context()

def get_bot_info():
    req = urllib.request.Request(
        f"{BASE_URL}/me",
        headers={"Authorization": TOKEN, "User-Agent": "MAX-Bot-Verifier/1.0"}
    )
    with urllib.request.urlopen(req, context=CTX, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))

def send_message(chat_id=None, user_id=None, text="Привет из бота!"):
    params = {}
    if chat_id:
        params["chat_id"] = chat_id
    elif user_id:
        params["user_id"] = user_id
    else:
        raise ValueError("Нужно указать chat_id или user_id")
    
    url = f"{BASE_URL}/messages?{urllib.parse.urlencode(params)}"
    payload = json.dumps({"text": text}).encode('utf-8')
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Authorization": TOKEN, "Content-Type": "application/json", "User-Agent": "MAX-Bot-Verifier/1.0"},
        method="POST"
    )
    with urllib.request.urlopen(req, context=CTX, timeout=10) as resp:
        return json.loads(resp.read().decode('utf-8'))

def run_test_listener():
    info = get_bot_info()
    print("=" * 60)
    print(f"✅ Бот успешно подключен к API MAX!")
    print(f"🤖 Имя:     {info.get('name') or info.get('first_name')}")
    print(f"🔗 Никнейм: @{info.get('username')}")
    print(f"🆔 ID бота: {info.get('id') or info.get('user_id')}")
    print("=" * 60)
    print(f"👉 Откройте мессенджер MAX, найдите бота @{info.get('username')} и напишите ему любое сообщение.")
    print("⏳ Ожидание сообщений (Long Polling)... Нажмите Ctrl+C для выхода.\n")
    
    marker = None
    while True:
        try:
            url = f"{BASE_URL}/updates"
            if marker:
                url += f"?marker={marker}"
            
            req = urllib.request.Request(
                url,
                headers={"Authorization": TOKEN, "User-Agent": "MAX-Bot-Verifier/1.0"}
            )
            with urllib.request.urlopen(req, context=CTX, timeout=35) as resp:
                data = json.loads(resp.read().decode('utf-8'))
                marker = data.get("marker", marker)
                updates = data.get("updates", [])
                
                for upd in updates:
                    upd_type = upd.get("update_type")
                    print(f"\n📩 Получено событие: [{upd_type}]")
                    
                    # Событие старта бота пользователем
                    if upd_type == "bot_started":
                        user = upd.get("user", {})
                        chat_id = upd.get("chat_id")
                        user_id = user.get("user_id") or user.get("id")
                        user_name = user.get("name") or user.get("first_name", "Пользователь")
                        print(f"👋 Бот запущен пользователем: {user_name} (ID: {user_id})")
                        
                        welcome_text = (
                            f"Здравствуйте, {user_name}!\n\n"
                            "Это тестовый ответ от вашего бота для хакатона «Умный город».\n"
                            "Бот успешно запущен и готов к разработке сценариев!"
                        )
                        send_message(chat_id=chat_id, user_id=user_id, text=welcome_text)
                        print("💬 Приветственное сообщение отправлено!")
                        
                    # Событие нового сообщения
                    elif upd_type == "message_created":
                        msg = upd.get("message", {})
                        body = msg.get("body", {})
                        text = body.get("text", "")
                        sender = msg.get("sender", {})
                        recipient = msg.get("recipient", {})
                        chat_id = recipient.get("chat_id")
                        user_id = sender.get("user_id") or sender.get("id")
                        
                        print(f"💬 Текст сообщения: \"{text}\" от {sender.get('first_name', 'User')} (ID: {user_id})")
                        
                        reply_text = f"Эхо-ответ: Я получил ваше сообщение «{text}»! Бот работает корректно 🚀"
                        send_message(chat_id=chat_id, user_id=user_id, text=reply_text)
                        print("💬 Эхо-ответ успешно отправлен в ответ!")
                        
        except urllib.error.HTTPError as e:
            print(f"HTTP Ошибка {e.code}: {e.reason}")
            time.sleep(3)
        except Exception as e:
            # Обычный таймаут long-polling или прерывание
            if "timed out" in str(e).lower():
                continue
            print("Ошибка соединения:", e)
            time.sleep(2)

if __name__ == "__main__":
    try:
        run_test_listener()
    except KeyboardInterrupt:
        print("\nОстановка тестового скрипта.")
