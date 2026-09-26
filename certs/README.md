# certs/

Здесь только **публичные** сертификаты удостоверяющих центров, приватных ключей нет и быть не должно.
Секреты (токен бота, ключи DaData и Yandex Cloud) лежат только в `.env` (не в git).

`russian_trusted_ca.pem` — цепочка НУЦ Минцифры России. `platform-api2.max.ru` подписан этим центром,
а в certifi его нет, поэтому клиент (`app/integrations/max_api.py: ssl_context()`) добавляет его к certifi.
TLS-проверка при этом не отключается. Без файла клиент MAX не запускается.

| Сертификат | Действует до | SHA-256 |
|---|---|---|
| Russian Trusted Root CA | 27.02.2032 | `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31` |
| Russian Trusted Sub CA  | 06.03.2027 | `BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B` |

Источник: https://www.gosuslugi.ru/crt. Отпечатки закреплены в `tests/test_security.py`: подмена файла роняет тесты.
При обновлении (Sub CA истекает в марте 2027) скачайте новые сертификаты оттуда же,
сверьте отпечатки с сайтом и обновите таблицу и тест.

Проверка: `openssl x509 -in certs/russian_trusted_ca.pem -noout -subject -enddate -fingerprint -sha256`
(показывает первый сертификат в файле).
