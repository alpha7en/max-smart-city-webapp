"""
Модуль компьютерного зрения для распознавания показаний приборов учета (CV Pipeline).
ВНИМАНИЕ: В соответствии с ТЗ данный модуль содержит [ЗАГЛУШКУ / CV MOCK],
которая эмулирует работу нейросетевого пайплайна (YOLOv8 + Perspective Correction + OCR).
"""

import time
import hashlib
from typing import Dict, Any, List

class MeterCVPipeline:
    """
    Эмулятор CV-пайплайна распознавания счетчиков:
    - Вода (ХВС / ГВС) с разделением черных кубов и красных литров
    - Электроэнергия (Однотарифный / Двухтарифный Т1-Т2)
    - Газ / Отопление
    - Серийный номер прибора учета
    """

    # Тарифы по умолчанию (Москва / Санкт-Петербург для расчета начислений)
    TARIFFS = {
        "ХВС": 50.93,   # руб / м³
        "ГВС": 243.16,  # руб / м³
        "СВЕТ_Т1": 6.73, # руб / кВт·ч (день)
        "СВЕТ_Т2": 2.16, # руб / кВт·ч (ночь)
        "ГАЗ": 7.85     # руб / м³
    }

    @staticmethod
    def process_meter_image(image_bytes: bytes = None, filename: str = "", meter_hint: str = None) -> Dict[str, Any]:
        """
        Обработка изображения прибора учета.
        [ЗАГЛУШКА ИИ]: Формирует реалистичные результаты детекции роликов и серийного номера.
        """
        time.sleep(0.3) # Эмуляция инференса нейросети (300 мс)
        
        # Определяем детерминированный или адаптивный сценарий по хэшу или подсказке
        h = int(hashlib.md5(filename.encode('utf-8') if filename else b"sample").hexdigest(), 16)
        
        if meter_hint:
            meter_hint = meter_hint.upper()
        
        # Сценарий 1: Счетчик горячей воды (ГВС)
        if meter_hint == "ГВС" or (not meter_hint and h % 3 == 0):
            meter_type = "ГВС"
            name = "Счетчик горячей воды Итэлма WFW20"
            serial_number = "2809142"
            integer_part = 142       # 5 черных роликов: 00142 м³
            decimal_part = 385       # 3 красных ролика: 385 литров
            total_reading = 142.385
            unit = "м³"
            prev_reading = 138.120
            delta = round(total_reading - prev_reading, 3)
            cost = round(delta * MeterCVPipeline.TARIFFS["ГВС"], 2)
            
        # Сценарий 2: Счетчик электроэнергии (Двухтарифный Т1/Т2)
        elif meter_hint in ["СВЕТ", "ЭЛЕКТРИЧЕСТВО"] or (not meter_hint and h % 3 == 1):
            meter_type = "ЭЛЕКТРОЭНЕРГИЯ_2Т"
            name = "Счетчик электроэнергии Меркурий 206"
            serial_number = "4819024"
            integer_part = 1845
            decimal_part = 6
            total_reading = 1845.6
            unit = "кВт·ч"
            prev_reading = 1710.0
            delta = round(total_reading - prev_reading, 1)
            # Т1 = 65%, Т2 = 35%
            cost = round((delta * 0.65 * MeterCVPipeline.TARIFFS["СВЕТ_Т1"]) + 
                         (delta * 0.35 * MeterCVPipeline.TARIFFS["СВЕТ_Т2"]), 2)
            
        # Сценарий 3: Счетчик холодной воды (ХВС)
        else:
            meter_type = "ХВС"
            name = "Счетчик холодной воды Бетар СХВ-15"
            serial_number = "5091823"
            integer_part = 89        # 00089 м³
            decimal_part = 720       # 720 литров
            total_reading = 89.720
            unit = "м³"
            prev_reading = 84.500
            delta = round(total_reading - prev_reading, 3)
            cost = round(delta * MeterCVPipeline.TARIFFS["ХВС"], 2)

        # Проверка аномалий
        is_anomaly = False
        anomaly_msg = None
        if delta > 30: # Аномально большой расход для квартиры
            is_anomaly = True
            anomaly_msg = f"Внимание! Расход ({delta} {unit}) превышает норму в 3 раза. Проверьте правильность распознавания."
        elif delta < 0:
            is_anomaly = True
            anomaly_msg = "Ошибка! Текущие показания меньше предыдущих (обратный ход счетчика)."

        return {
            "status": "success",
            "is_mock": True,
            "stub_notice": "[ЗАГЛУШКА ИИ / CV MOCK: эмуляция вывода детекции YOLOv8 + OCR роликов]",
            "meter_type": meter_type,
            "meter_name": name,
            "serial_number": serial_number,
            "readings": {
                "integer_part": integer_part,
                "decimal_part": decimal_part,
                "formatted_reading": f"{integer_part}.{str(decimal_part).zfill(3) if meter_type != 'ЭЛЕКТРОЭНЕРГИЯ_2Т' else decimal_part}",
                "value": total_reading,
                "unit": unit
            },
            "rollers": {
                "black_digits": [int(d) for d in str(integer_part).zfill(5)],
                "red_digits": [int(d) for d in str(decimal_part).zfill(3)] if meter_type != 'ЭЛЕКТРОЭНЕРГИЯ_2Т' else [decimal_part]
            },
            "confidence": 0.984,
            "perspective_corrected": True,
            "calculation": {
                "previous_reading": prev_reading,
                "delta": delta,
                "tariff_rate": MeterCVPipeline.TARIFFS.get(meter_type, 50.0),
                "estimated_rub": cost
            },
            "anomaly": {
                "is_detected": is_anomaly,
                "message": anomaly_msg
            }
        }
