"""Оценка качества на датасете Yandex.Toloka Water Meters (Kaggle: tapakah68/yandextoloka-water-meters-dataset).

Пример:
    python scripts/eval_water.py --data data/WaterMeters --limit 100 --concurrency 8
"""
import argparse
import asyncio
import csv
import random
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from meter_reader.config import Settings  # noqa: E402
from meter_reader.recognizer import MeterRecognizer  # noqa: E402


def load_labels(data_dir: Path) -> list[tuple[str, float]]:
    with open(data_dir / "data.csv", newline="") as f:
        return [(row["photo_name"], float(row["value"])) for row in csv.DictReader(f)]


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--data", type=Path, default=Path("data/WaterMeters"))
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--concurrency", type=int, default=8)
    parser.add_argument("--thinking", action="store_true", help="включить режим размышлений модели")
    parser.add_argument("--out", type=Path, default=Path("results/eval_water.csv"))
    args = parser.parse_args()

    labels = load_labels(args.data)
    random.Random(args.seed).shuffle(labels)
    labels = labels[: args.limit]

    settings = Settings(enable_thinking=args.thinking)
    recognizer = MeterRecognizer(settings)
    semaphore = asyncio.Semaphore(args.concurrency)

    async def run_one(name: str, expected: float) -> dict:
        async with semaphore:
            started = time.monotonic()
            try:
                result = await recognizer.recognize((args.data / "images" / name).read_bytes())
                error = ""
            except Exception as exc:  # noqa: BLE001 - собираем все ошибки для отчёта
                result, error = None, str(exc)[:200]
            elapsed = time.monotonic() - started

        predicted = result.reading if result else None
        return {
            "photo": name,
            "expected": expected,
            "predicted": predicted,
            "exact": predicted is not None and abs(predicted - expected) < 1e-6,
            "integer_ok": predicted is not None and int(predicted) == int(expected),
            "abs_error": abs(predicted - expected) if predicted is not None else "",
            "meter_type": result.meter_type if result else "",
            "brand": result.brand if result else "",
            "model": result.model if result else "",
            "confidence": result.confidence if result else "",
            "seconds": round(elapsed, 2),
            "error": error,
        }

    started = time.monotonic()
    rows = await asyncio.gather(*(run_one(n, v) for n, v in labels))
    total_time = time.monotonic() - started
    await recognizer.aclose()

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    n = len(rows)
    ok = [r for r in rows if not r["error"]]
    print(f"Изображений: {n}, ошибок API/парсинга: {n - len(ok)}, время: {total_time:.0f} c")
    print(f"Точное совпадение показания:  {sum(r['exact'] for r in rows) / n:.1%}")
    print(f"Совпадение целой части (м³): {sum(r['integer_ok'] for r in rows) / n:.1%}")
    print(f"Среднее время на фото: {sum(r['seconds'] for r in rows) / n:.1f} c")
    print("Типы:", dict(Counter(r["meter_type"] for r in ok)))
    print("Бренды:", Counter(r["brand"] for r in ok).most_common(10))
    print("Модели:", Counter(r["model"] for r in ok).most_common(10))
    print(f"Детали: {args.out}")


if __name__ == "__main__":
    asyncio.run(main())
