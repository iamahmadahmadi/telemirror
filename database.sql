PRAGMA foreign_keys=OFF;
BEGIN TRANSACTION;
CREATE TABLE users (
  user_id        INTEGER PRIMARY KEY,
  daily_usage_mb INTEGER NOT NULL DEFAULT 0,
  premium_active INTEGER NOT NULL DEFAULT 0,   -- 0=free, 31=month, 365=year
  referrals      INTEGER NOT NULL DEFAULT 0,
  referrer       INTEGER,
  ads_watched    INTEGER NOT NULL DEFAULT 0
);
INSERT INTO users VALUES(12345,0,0,0,NULL,0);
INSERT INTO users VALUES(84895335,324,0,0,NULL,0);
INSERT INTO users VALUES(130475620,0,0,0,NULL,0);
INSERT INTO users VALUES(1344528950,309,0,0,NULL,0);
CREATE TABLE files (
  file_id        TEXT PRIMARY KEY,  -- Telegram file_id
  name           TEXT,
  leecher_id     INTEGER NOT NULL,
  timestamp      INTEGER NOT NULL,
  download_count INTEGER NOT NULL DEFAULT 0,
  nsfw           INTEGER NOT NULL DEFAULT 0,  -- 0/1
  thumbnail      TEXT,
  quality        TEXT
);
INSERT INTO files VALUES('BAACAgEAAxkDAAIBp2ihVUIuA6SwZxqwWXiuw3CwO_XMAAJmBQACsuQQRU2x9J7LLxwAAR4E','عیدی اچ دی - قسمت 5 (ابوطالب حسینی) 360p25fps 234.mp4',84895335,1755403586,0,0,'/usr/src/app/downloads/416/yt-dlp-thumb/عیدی اچ دی - قسمت 5 (ابوطالب حسینی) 360p25fps 234.jpg','');
INSERT INTO files VALUES('BAACAgEAAxkDAAIBqWihVXnyL_ARNDHp32DF63rNeUoWAAIzBQACWyAIRUKv7uIqs6U0HgQ','عیدی اچ دی - قسمت 5 (ابوطالب حسینی) 360p25fps 234.mp4',1344528950,1755403641,0,0,'/usr/src/app/downloads/420/yt-dlp-thumb/عیدی اچ دی - قسمت 5 (ابوطالب حسینی) 360p25fps 234.jpg','');
CREATE INDEX idx_files_leecher_id ON files(leecher_id);
COMMIT;
