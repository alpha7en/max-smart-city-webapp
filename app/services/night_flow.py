"""
Night Flow Analysis & Crowd-Diagnostics Service ("Ночной дозор").
Complies with 261-FZ (Energy efficiency & building meters - ОДПУ).
Detects night water imbalance (02:30 - 04:30 AM) and crowdsources leak localization with resident incentives.
"""

from typing import Optional
from app.models.schemas import (
    NightFlowCheckRequest,
    NightFlowDiagnosticResponse
)

class NightFlowService:
    def diagnose_leak(self, req: NightFlowCheckRequest) -> NightFlowDiagnosticResponse:
        """
        Analyzes building-level entrance flow telemetry (ODPU)
        combined with individual apartment tests (napkin test or bedtime/morning readings).
        """
        # Simulated entrance telemetry: building ODPU records 1,650 L/h during deep night (02:30-04:30)
        odpu_flow = 1650.0
        normal_threshold = 100.0
        building_leak = odpu_flow > normal_threshold

        apartment_leak = False
        diff_liters = 0.0
        reward_eligible = False

        # Case 1: Bedtime vs Morning reading comparison
        if req.night_reading_before_bed is not None and req.morning_reading is not None:
            # readings are in m³
            diff_m3 = req.morning_reading - req.night_reading_before_bed
            diff_liters = round(diff_m3 * 1000.0, 1)
            if diff_liters > 20.0:  # more than 20 liters while sleeping
                apartment_leak = True
                reward_eligible = True

        # Case 2: Napkin test
        if req.napkin_test_result == "wet":
            apartment_leak = True
            reward_eligible = True
            if diff_liters == 0.0:
                diff_liters = 180.0  # typical toilet valve leak per night

        if apartment_leak:
            verdict = (
                f"[ОДПУ: ВНИМАНИЕ] Утечка обнаружена в вашей квартире! "
                f"Зафиксирована потеря около {diff_liters} л воды за ночь. "
                f"Основная причина в 85% случаев — неисправность впускного/выпускного клапана бачка унитаза."
            )
            recommendation = (
                "Поздравляем! Вы помогли дому предотвратить перерасход ОДН. "
                "Вам начислена скидка 10% на содержание жилья в следующем месяце! "
                "Вызовите сантехника УК в один клик через раздел «Заявки» для бесплатного устранения утечки."
            )
        elif building_leak:
            verdict = (
                f"В вашем подъезде №{req.entrance_id} общедомовой счетчик (ОДПУ) фиксирует аномальный расход: "
                f"{odpu_flow} л/час (норма: до {normal_threshold} л/час). "
                f"В вашей квартире утечки не обнаружено. Спасибо за участие в мониторинге!"
            )
            recommendation = "Ваша сантехника в порядке. Сервис продолжает опрос соседних квартир по стояку."
        else:
            verdict = "Ночной баланс дома в идеальной норме. Утечек не зафиксировано."
            recommendation = "Никаких действий не требуется."

        return NightFlowDiagnosticResponse(
            entrance_odpu_flow_liters_per_hour=odpu_flow,
            normal_threshold_liters_per_hour=normal_threshold,
            leak_detected_in_building=building_leak,
            apartment_leak_detected=apartment_leak,
            apartment_difference_liters=diff_liters,
            diagnosis_verdict=verdict,
            reward_eligible=reward_eligible,
            recommendation=recommendation
        )

night_flow_service = NightFlowService()
