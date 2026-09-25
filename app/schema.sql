-- Схема v1 (дальнейшие версии — MIGRATIONS в db.py). Значения показаний — INTEGER в тысячных долях.
CREATE TABLE users(
  id INTEGER PRIMARY KEY, max_user_id INTEGER UNIQUE NOT NULL, chat_id INTEGER,
  full_name TEXT, phone TEXT, phone_verified INTEGER NOT NULL DEFAULT 0,
  registered_at TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE addresses(
  id INTEGER PRIMARY KEY, norm_key TEXT UNIQUE NOT NULL,
  status TEXT NOT NULL CHECK(status IN('verified_flat','verified_house','unverified')),
  source TEXT NOT NULL CHECK(source IN('dadata','local')),
  full_text TEXT NOT NULL, postal_code TEXT, region TEXT, locality TEXT, street TEXT,
  house TEXT, block TEXT, flat TEXT, house_fias_id TEXT, flat_fias_id TEXT, fias_id TEXT,
  oktmo TEXT, house_cadnum TEXT, geo_lat REAL, geo_lon REAL, raw_json TEXT,
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE TABLE user_addresses(
  user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  address_id INTEGER NOT NULL REFERENCES addresses(id),
  role TEXT NOT NULL CHECK(role IN('owner','tenant')),
  access TEXT NOT NULL CHECK(access IN('granted','pending','denied')),
  label TEXT NOT NULL, raw_input TEXT, created_at TEXT NOT NULL DEFAULT (datetime('now')),
  PRIMARY KEY(user_id, address_id));
CREATE TABLE meters(
  id INTEGER PRIMARY KEY, address_id INTEGER NOT NULL REFERENCES addresses(id),
  type TEXT NOT NULL CHECK(type IN('cold_water','hot_water','electricity','gas','heat')),
  tariffs INTEGER NOT NULL DEFAULT 1 CHECK(tariffs BETWEEN 1 AND 3),
  serial TEXT, serial_norm TEXT,                                                  -- serial для показа, serial_norm для сравнения
  verification_due TEXT, verification_source TEXT CHECK(verification_source IN('user','model')),
  created_by INTEGER REFERENCES users(id) ON DELETE SET NULL, active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE UNIQUE INDEX meters_serial ON meters(address_id, serial_norm) WHERE serial_norm IS NOT NULL;
CREATE TABLE readings(
  id INTEGER PRIMARY KEY, meter_id INTEGER NOT NULL REFERENCES meters(id),
  user_id INTEGER REFERENCES users(id) ON DELETE SET NULL, period TEXT NOT NULL,  -- 'YYYY-MM'
  t1 INTEGER NOT NULL, t2 INTEGER, t3 INTEGER,                                    -- тысячные доли
  source TEXT NOT NULL CHECK(source IN('photo','photo_edited','manual','miniapp')),
  recognized_json TEXT, status TEXT NOT NULL CHECK(status IN('accepted','flagged','replaced')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')));
CREATE UNIQUE INDEX readings_period ON readings(meter_id, period) WHERE status != 'replaced';
CREATE TABLE sessions(user_id INTEGER PRIMARY KEY, state TEXT NOT NULL, data TEXT NOT NULL DEFAULT '{}',
  flow_id TEXT NOT NULL, updated_at TEXT NOT NULL, expires_at TEXT);
CREATE TABLE photos(id TEXT PRIMARY KEY, user_id INTEGER NOT NULL, path TEXT NOT NULL,
  created_at TEXT NOT NULL, expires_at TEXT NOT NULL);
CREATE TABLE bills(id INTEGER PRIMARY KEY, address_id INTEGER NOT NULL REFERENCES addresses(id),
  period TEXT NOT NULL, amount_kop INTEGER NOT NULL, due_date TEXT NOT NULL,
  status TEXT NOT NULL CHECK(status IN('unpaid','paid')), is_demo INTEGER NOT NULL DEFAULT 1,
  UNIQUE(address_id, period));
CREATE TABLE notifications(id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, kind TEXT NOT NULL,
  dedup_key TEXT NOT NULL, sent_at TEXT NOT NULL, UNIQUE(user_id, kind, dedup_key));
CREATE TABLE kv(key TEXT PRIMARY KEY, value TEXT);
