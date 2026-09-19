#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Демонстрационный и проверочный скрипт для хакатона ЖКХ:
1. Валидатор и генератор платежных строк ГОСТ Р 56042-2014 (QR-код оплаты ЖКХ);
2. Проверка контрольного разряда расчетного счета Банка России;
3. Бизнес-логика валидации счетчиков воды (отсечение красных роликов / литров и проверка аномалий).
"""

import sys

def validate_account_checksum(account_20: str, bic_9: str) -> bool:
    """
    Проверка контрольного ключа расчетного счета по алгоритму Банка России.
    Ключ находится на 9-й позиции (индекс 8 в 0-indexed).
    """
    if len(account_20) != 20 or len(bic_9) != 9:
        return False
    
    # Берем последние 3 цифры БИК + 20 цифр счета (с заменой 9-го знака счета на 0)
    bic_tail = bic_9[-3:]
    check_str = bic_tail + account_20[:8] + "0" + account_20[9:]
    
    weights = [7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1, 3, 7, 1]
    
    total_sum = sum(int(digit) * w for digit, w in zip(check_str, weights))
    calc_key = (total_sum % 10 * 3) % 10
    
    expected_key = int(account_20[8])
    return calc_key == expected_key


def parse_gost_qr(qr_string: str) -> dict:
    """
    Парсит строку QR-кода по ГОСТ Р 56042-2014.
    """
    if not (qr_string.startswith("ST00011|") or qr_string.startswith("ST00012|")):
        raise ValueError("Строка не является валидным платежным кодом ГОСТ Р 56042-2014 (нет заголовка ST00011/ST00012)")
    
    parts = qr_string.split("|")
    header = parts[0]
    fields = {}
    for p in parts[1:]:
        if "=" in p:
            k, v = p.split("=", 1)
            fields[k] = v
            
    # Проверка обязательных полей
    required = ["Name", "PersonalAcc", "BankName", "BIC", "CorrespAcc"]
    missing = [r for r in required if r not in fields]
    if missing:
        raise ValueError(f"Отсутствуют обязательные поля ГОСТ: {missing}")
        
    return {"header": header, "fields": fields}


def simulate_meter_reading(raw_digits: list, colors: list, previous_val: float) -> dict:
    """
    Моделирует работу Computer Vision модуля счетчика воды:
    raw_digits: распознанные цифры роликов слева направо (обычно 8 цифр)
    colors: цвета роликов для каждого разряда (черный: B, красный: R)
    previous_val: показания за предыдущий месяц (в целых кубах)
    """
    black_digits = [d for d, c in zip(raw_digits, colors) if c == "B"]
    red_digits = [d for d, c in zip(raw_digits, colors) if c == "R"]
    
    cubic_meters = int("".join(map(str, black_digits))) if black_digits else 0
    liters = int("".join(map(str, red_digits))) if red_digits else 0
    total_m3 = cubic_meters + (liters / 1000.0)
    
    # Проверка на монотонность и дельту
    delta = cubic_meters - int(previous_val)
    warning = None
    if delta < 0:
        warning = f"ОШИБКА: Текущие показания ({cubic_meters}) меньше предыдущих ({previous_val})!"
    elif delta > 50:
        warning = f"ВНИМАНИЕ: Аномально высокий расход (+{delta} м³). Возможна ошибка или утечка."
        
    return {
        "cubic_meters": cubic_meters,
        "liters": liters,
        "total_exact_m3": total_m3,
        "delta_m3": delta,
        "warning": warning
    }


if __name__ == "__main__":
    print("=== ТЕСТ 1: Парсинг и валидация реального ЖКХ QR-кода (ГОСТ Р 56042-2014) ===")
    sample_qr = (
        "ST00012|Name=ООО УК ДОМ-СЕРВИС|PersonalAcc=40821810438000005432|"
        "BankName=ПАО СБЕРБАНК|BIC=044525225|CorrespAcc=30101810400000000225|"
        "PayeeINN=7701987654|KPP=770101001|Sum=725040|Purpose=Оплата ЖКУ за 08.2026|"
        "PersAcc=7701234567|PaymPeriod=082026|PayerAddress=г. Москва, ул. Тверская, 10|TechCode=01"
    )
    
    parsed = parse_gost_qr(sample_qr)
    print(f"Заголовок: {parsed['header']}")
    print(f"Получатель: {parsed['fields']['Name']}")
    print(f"Счет получателя: {parsed['fields']['PersonalAcc']} (Спецсчет 40821 платежного агента)")
    print(f"Сумма к оплате: {float(parsed['fields']['Sum'])/100:.2f} руб.")
    
    print("\n=== ТЕСТ 2: Проверка контрольного ключа расчетного счета ===")
    acc = parsed["fields"]["PersonalAcc"]
    bic = parsed["fields"]["BIC"]
    is_valid = validate_account_checksum(acc, bic)
    print(f"Счет {acc} валиден для БИК {bic}: {is_valid}")
    
    print("\n=== ТЕСТ 3: Демонстрация проблемы красных роликов (литров) ===")
    # Пример: на счетчике 00142 (черные) и 750 (красные)
    digits = [0, 0, 1, 4, 2, 7, 5, 0]
    colors = ["B", "B", "B", "B", "B", "R", "R", "R"]
    prev_reading = 138.0
    
    # Если наивный OCR распознает все 8 цифр:
    naive_val = int("".join(map(str, digits)))
    print(f"НАИВНЫЙ OCR (без разделения цветов): {naive_val} м³  <-- ОШИБКА В 1000 РАЗ!")
    
    # Умная фильтрация:
    smart_res = simulate_meter_reading(digits, colors, prev_reading)
    print(f"УМНЫЙ CV-ПАЙПЛАЙН: {smart_res['cubic_meters']} м³ (литры {smart_res['liters']} л успешно отсечены)")
    print(f"Расход за месяц: +{smart_res['delta_m3']} м³ | Предупреждения: {smart_res['warning']}")
    print("\nВсе проверки успешно пройдены!")
