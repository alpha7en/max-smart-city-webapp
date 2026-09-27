# certs/

`russian_trusted_ca.pem` — **публичные** сертификаты НУЦ Минцифры России. `platform-api2.max.ru` подписан этим
центром, а в certifi его нет, поэтому клиенты MAX и ФГИС (`app/integrations/max_api.py: ssl_context()`)
добавляют его к certifi. TLS-проверка при этом не отключается; без файла бот не запустится.
Приватных ключей здесь нет и быть не должно — секреты живут только в `.env`.

| Сертификат (порядок в файле) | Действует до | SHA-256 |
|---|---|---|
| Russian Trusted Sub CA | 06.03.2027 | `BB:BD:E2:10:3E:79:0B:99:9E:C6:2B:D0:3C:F6:25:A5:A2:E7:C3:16:E1:0A:FE:6A:49:0E:ED:EA:D8:B3:FD:9B` |
| Russian Trusted Root CA | 27.02.2032 | `D2:6D:2D:02:31:B7:C3:9F:92:CC:73:85:12:BA:54:10:35:19:E4:40:5D:68:B5:BD:70:3E:97:88:CA:8E:CF:31` |

Источник — https://www.gosuslugi.ru/crt. Sub CA истекает в марте 2027: скачайте новые сертификаты оттуда же,
сверьте отпечатки с сайтом и обновите таблицу.

Проверить файл:

```bash
openssl crl2pkcs7 -nocrl -certfile certs/russian_trusted_ca.pem | openssl pkcs7 -print_certs -noout
python -c "import re,ssl,hashlib; pem=open('certs/russian_trusted_ca.pem').read(); \
[print(hashlib.sha256(ssl.PEM_cert_to_DER_cert(c)).hexdigest()) for c in re.findall('-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----', pem, re.S)]"
```
