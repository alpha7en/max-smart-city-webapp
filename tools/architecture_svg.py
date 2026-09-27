"""Схема инфраструктуры для README: светлая и тёмная версии SVG из одной геометрии.

    python -m tools.architecture_svg      # → docs/img/architecture-light.svg, docs/img/architecture-dark.svg
"""
from __future__ import annotations

from pathlib import Path
from xml.sax.saxutils import escape

OUT = Path(__file__).resolve().parents[1] / "docs" / "img"
W, H = 1240, 500
FONT = "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'Noto Sans', Helvetica, Arial, sans-serif"

# Палитра GitHub Primer. Фон прозрачный: схема лежит на фоне README (page — его цвет, им же подложены подписи).
THEMES = {
    "light": {
        "page": "#ffffff", "zone_line": "#afb8c1", "node": "#f6f8fa", "node_line": "#d0d7de",
        "title": "#1f2328", "muted": "#59636e", "arrow": "#6e7781", "accent": "#0969da", "accent_bg": "#ddf4ff",
    },
    "dark": {
        "page": "#0d1117", "zone_line": "#3d444d", "node": "#161b22", "node_line": "#3d444d",
        "title": "#f0f6fc", "muted": "#9198a1", "arrow": "#9198a1", "accent": "#4493f8", "accent_bg": "#1b2b44",
    },
}


class Svg:
    def __init__(self, c: dict[str, str]):
        self.c, self.parts = c, []

    def text(self, x: float, y: float, s: str, *, size: int = 13, color: str = "title", weight: int = 400,
             anchor: str = "middle", spacing: float = 0) -> None:
        extra = f' letter-spacing="{spacing}"' if spacing else ""
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" font-weight="{weight}" fill="{self.c[color]}" '
                          f'text-anchor="{anchor}"{extra}>{escape(s)}</text>')

    def zone(self, x: float, y: float, w: float, h: float, label: str) -> None:
        """Граница: пунктирная рамка и подпись в левом верхнем углу."""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="14" fill="none" '
                          f'stroke="{self.c["zone_line"]}" stroke-dasharray="6 5"/>')
        self.text(x + 16, y + 24, label.upper(), size=11, color="muted", weight=600, anchor="start", spacing=0.8)

    def node(self, x: float, y: float, w: float, h: float, title: str, sub: str = "", *, ours: bool = False) -> None:
        """Компонент: карточка с заголовком и подписью; наши сервисы — с акцентной полосой слева."""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="10" fill="{self.c["node"]}" '
                          f'stroke="{self.c["accent"] if ours else self.c["node_line"]}"/>')
        if ours:
            self.parts.append(f'<path d="M{x + 1} {y + 10} a9 9 0 0 1 9 -9 h1 v{h - 2} h-1 a9 9 0 0 1 -9 -9 z" '
                              f'fill="{self.c["accent"]}"/>')
        cx = x + w / 2
        if sub:
            self.text(cx, y + h / 2 - 3, title, size=14, weight=600)
            self.text(cx, y + h / 2 + 15, sub, size=12, color="muted")
        else:
            self.text(cx, y + h / 2 + 5, title, size=14, weight=600)

    def chip(self, x: float, y: float, w: float, s: str) -> None:
        """Часть процесса app: плашка внутри контейнера."""
        self.parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="26" rx="6" fill="{self.c["accent_bg"]}"/>')
        self.text(x + w / 2, y + 17, s, size=12)

    def database(self, x: float, y: float, w: float, h: float, title: str, sub: str) -> None:
        """Хранилище: цилиндр."""
        ry, c = 7, self.c
        self.parts.append(
            f'<path d="M{x} {y + ry} a{w / 2} {ry} 0 0 1 {w} 0 v{h - 2 * ry} a{w / 2} {ry} 0 0 1 {-w} 0 z" '
            f'fill="{c["node"]}" stroke="{c["node_line"]}"/>'
            f'<path d="M{x} {y + ry} a{w / 2} {ry} 0 0 0 {w} 0" fill="none" stroke="{c["node_line"]}"/>')
        self.text(x + w / 2, y + h / 2 + 3, title, size=14, weight=600)
        self.text(x + w / 2, y + h / 2 + 20, sub, size=12, color="muted")

    def arrow(self, a: tuple[float, float], b: tuple[float, float], label: str = "", *, both: bool = False) -> None:
        """Прямая стрелка a → b. Подпись горизонтальной — над серединой, на подложке цвета фона (скрывает
        пунктир зон под текстом); вертикальной — справа от линии."""
        start = ' marker-start="url(#head)"' if both else ""
        self.parts.append(f'<path d="M{a[0]} {a[1]} L{b[0]} {b[1]}" fill="none" stroke="{self.c["arrow"]}" '
                          f'stroke-width="1.5"{start} marker-end="url(#head)"/>')
        if not label:
            return
        if a[1] == b[1]:
            x, y, w = (a[0] + b[0]) / 2, a[1] - 9, 7 * len(label) + 8
            self.parts.append(f'<rect x="{x - w / 2}" y="{y - 13}" width="{w}" height="17" fill="{self.c["page"]}"/>')
            self.text(x, y, label, size=12, color="muted")
        else:
            self.text(a[0] + 8, (a[1] + b[1]) / 2 + 4, label, size=12, color="muted", anchor="start")

    def render(self) -> str:
        head = (f'<marker id="head" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" '
                f'orient="auto-start-reverse"><path d="M0 0 L10 5 L0 10 z" fill="{self.c["arrow"]}"/></marker>')
        return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
                f'font-family="{FONT}" role="img" aria-label="Инфраструктура бота ЖКХ в MAX">'
                f'<defs>{head}</defs>' + "".join(self.parts) + "</svg>\n")


def draw(theme: str) -> str:
    s = Svg(THEMES[theme])
    # Зоны слева направо: пользователь → платформа MAX → наш сервер → внешние сервисы.
    # Сетка рядов: 150 — чат и long polling, 270 — мини-приложение и /api, 400 — хранилище и распознавание.
    s.zone(20, 20, 200, 460, "Житель в MAX")
    s.zone(260, 20, 190, 460, "Платформа MAX")
    s.zone(480, 20, 530, 460, "Сервер maxsmartcity.ru")
    s.zone(690, 60, 300, 400, "Docker Compose")
    s.zone(1040, 20, 180, 460, "Внешние сервисы")

    s.node(40, 118, 160, 64, "Чат с ботом", "фото, кнопки")
    s.node(40, 238, 160, 64, "Мини-приложение", "WebView в MAX")
    s.node(280, 118, 150, 64, "MAX Bot API", "platform-api2.max.ru")
    s.node(500, 238, 150, 64, "nginx", "HTTPS :443")
    s.node(500, 368, 150, 64, "Статика", "index.html, app.js")

    s.node(710, 96, 260, 194, "", ours=True)
    s.text(840, 124, "app", size=14, weight=600)
    s.text(840, 142, "Python · FastAPI :8080", size=12, color="muted")
    s.chip(728, 166, 228, "бот: long polling MAX")
    s.chip(728, 203, 228, "API мини-приложения")
    s.chip(728, 240, 228, "планировщик уведомлений")
    s.database(710, 366, 100, 70, "SQLite", "том ./data")
    s.node(840, 368, 130, 64, "recognizer", "FastAPI :8000", ours=True)

    s.node(1060, 118, 140, 64, "ФГИС «Аршин»", "реестр поверок")
    s.node(1060, 238, 140, 64, "DaData", "адреса, ФИАС")
    s.node(1060, 368, 140, 64, "Yandex Cloud", "Qwen, AI Studio")

    s.arrow((200, 150), (280, 150), "сообщения", both=True)
    s.arrow((710, 150), (430, 150), "long polling · ответы")
    s.arrow((200, 270), (500, 270), "HTTPS · initData")
    s.arrow((650, 270), (710, 270), "/api")
    s.arrow((575, 302), (575, 368), "/")
    s.arrow((760, 290), (760, 366), "данные")
    s.arrow((905, 290), (905, 368), "фото")
    s.arrow((970, 150), (1060, 150), "HTTPS")
    s.arrow((970, 270), (1060, 270), "HTTPS")
    s.arrow((970, 400), (1060, 400), "HTTPS")
    return s.render()


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for theme in THEMES:
        (OUT / f"architecture-{theme}.svg").write_text(draw(theme), encoding="utf-8")


if __name__ == "__main__":
    main()
