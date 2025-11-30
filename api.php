<?php
/**
 * Simple JSON API for Telemirror + Crypto Bot webhook (users.premium_active version)
 * - Webhook auth (token / secret / IP allowlist / relaxed)
 * - Structured logging
 * - Uses single `users` table with columns:
 *     user_id, daily_usage_mb, premium_active, referrals, referrer, ads_watched
 * - Sends a dynamic Telegram receipt message (`sampleBuyReceipt`) after successful application
 */

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store, no-cache, must-revalidate, max-age=0');
header('Access-Control-Allow-Origin: *');

// ---- PHP error logging safety net ----
@ini_set('log_errors', '1');
if (!ini_get('error_log')) {
  @ini_set('error_log', __DIR__ . '/php-error.log');
}

// ========================= CONFIG =========================
$DB_DRIVER   = getenv('TM_API_DB_DRIVER')      ?: 'sqlite';
$IS_SQLITE   = ($DB_DRIVER === 'sqlite');
$SQLITE_PATH = getenv('TM_API_SQLITE_PATH')    ?: (__DIR__ . '/botdata.sqlite3');

$MYSQL_HOST   = getenv('TM_API_MYSQL_HOST')    ?: '127.0.0.1';
$MYSQL_DB     = getenv('TM_API_MYSQL_DB')      ?: 'telemirror';
$MYSQL_USER   = getenv('TM_API_MYSQL_USER')    ?: 'root';
$MYSQL_PASS   = getenv('TM_API_MYSQL_PASS')    ?: '';
$MYSQL_CHARSET= 'utf8mb4';

$API_KEY = getenv('TM_API_KEY') ?: '';

$TBL_USERS    = 'users';
$TBL_PAYMENTS = 'payments';

// ---- Bot / Telegram ----
$BOT_TOKEN     = getenv('BOT_TOKEN')     ?: '5310077920:AAGf8wtBapssFrIXyt6_1rsI0x25MZBnI5g';
$BOT_USERNAME  = getenv('BOT_USERNAME')  ?: '@LeechFlixBot';

// ---- Crypto Pay webhook/auth config ----
$CRYPTO_PAY_TOKEN       = getenv('CRYPTO_PAY_TOKEN') ?: '166461:AAnJqbkNIdfbzaXO4swVoAYIjRepGJMAv5N';
$CRYPTO_WEBHOOK_SECRET  = getenv('CRYPTO_WEBHOOK_SECRET') ?: '';  // optional shared secret
$WEBHOOK_AUTH_MODE      = strtolower(getenv('TM_API_WEBHOOK_AUTH') ?: 'relaxed'); // strict|relaxed
$TRUSTED_IPS_STR        = getenv('TM_API_TRUSTED_IPS') ?: '104.248.195.90';
$TRUSTED_IPS            = array_filter(array_map('trim', explode(',', $TRUSTED_IPS_STR)));

date_default_timezone_set(getenv('TM_API_TZ') ?: 'UTC');

/** Basic Telegram API helpers */
function apiRequest($method, $queries = []) {
    global $BOT_TOKEN;
    $cp = curl_init('https://api.telegram.org/bot' . $BOT_TOKEN . '/' . $method);
    curl_setopt_array($cp, [
        CURLOPT_POST            => true,
        CURLOPT_RETURNTRANSFER  => true,
        CURLOPT_POSTFIELDS      => $queries,
        CURLOPT_CONNECTTIMEOUT  => 10,
        CURLOPT_TIMEOUT         => 30,
    ]);
    $raw = curl_exec($cp);
    $err = curl_error($cp);
    $code = curl_getinfo($cp, CURLINFO_HTTP_CODE);
    curl_close($cp);
    $decoded = json_decode($raw, true);
    if ($err) {
        logline('warn', 'telegram.http.error', ['error'=>$err, 'code'=>$code, 'raw'=>$raw]);
    } else {
        logline('info', 'telegram.http', ['code'=>$code]);
    }
    return is_array($decoded) ? $decoded : ['ok'=>false, 'error_code'=>$code, 'description'=>$err ?: 'decode_failed', 'raw'=>$raw];
}
function sendMessage($text, $keyboard = null, $id = null, $parse_mode = 'HTML', $msg = null, $thread = null) {
    $payload = [
        'chat_id'                  => $id,
        'text'                     => $text,
        'parse_mode'               => $parse_mode,
        'disable_web_page_preview' => true,
    ];
    if ($keyboard !== null) {
        $payload['reply_markup'] = is_string($keyboard) ? $keyboard : json_encode($keyboard);
    }
    if ($msg !== null)    $payload['reply_to_message_id'] = $msg;
    if ($thread !== null) $payload['message_thread_id']   = $thread;

    $resp = apiRequest('sendMessage', $payload);
    if (!empty($resp['ok'])) {
        logline('info', 'telegram.send.ok', ['message_id'=>$resp['result']['message_id'] ?? null, 'chat_id'=>$id]);
    } else {
        logline('warn', 'telegram.send.fail', ['chat_id'=>$id, 'resp'=>$resp]);
    }
    return $resp;
}

// ========================= LOGGING =========================
function logline($level, $tag, array $ctx = []) {
    $rid = isset($GLOBALS['_req_id']) ? $GLOBALS['_req_id'] : substr(bin2hex(random_bytes(6)), 0, 12);
    $ctx_json = $ctx ? json_encode($ctx, JSON_UNESCAPED_SLASHES|JSON_UNESCAPED_UNICODE) : '';
    error_log(sprintf('[api][%s][%s] %s %s', $rid, strtoupper($level), $tag, $ctx_json));
}
$GLOBALS['_req_id'] = substr(hash('xxh128', microtime(true) . random_bytes(8)), 0, 12);

// ========================= HELPERS =========================
function json_out($status, $data = [], $http = 200) {
    http_response_code($http);
    echo json_encode(['status' => $status] + $data, JSON_UNESCAPED_SLASHES | JSON_UNESCAPED_UNICODE);
    exit;
}
function bad_request($msg, $http=400) {
    logline('warn', 'bad_request: '.$msg);
    json_out('error', ['error' => $msg], $http);
}
function require_key($API_KEY) {
    if ($API_KEY === '') return;
    $given = isset($_GET['key']) ? (string)$_GET['key'] : '';
    if (!hash_equals($API_KEY, $given)) {
        bad_request('invalid_or_missing_api_key', 401);
    }
}
function pdo_connect($DB_DRIVER, $SQLITE_PATH, $MYSQL_HOST, $MYSQL_DB, $MYSQL_USER, $MYSQL_PASS, $MYSQL_CHARSET) {
    try {
        if ($DB_DRIVER === 'sqlite') {
            if (!is_file($SQLITE_PATH)) @mkdir(dirname($SQLITE_PATH), 0775, true);
            $pdo = new PDO("sqlite:" . $SQLITE_PATH);
        } else {
            $pdo = new PDO(
                "mysql:host={$MYSQL_HOST};dbname={$MYSQL_DB};charset={$MYSQL_CHARSET}",
                $MYSQL_USER, $MYSQL_PASS,
                [PDO::ATTR_ERRMODE=>PDO::ERRMODE_EXCEPTION, PDO::ATTR_DEFAULT_FETCH_MODE=>PDO::FETCH_ASSOC]
            );
        }
        $pdo->setAttribute(PDO::ATTR_ERRMODE, PDO::ERRMODE_EXCEPTION);
        logline('info', 'db.connected', ['driver'=>$pdo->getAttribute(PDO::ATTR_DRIVER_NAME)]);
        return $pdo;
    } catch (Throwable $e) {
        bad_request('db_connect_failed: ' . $e->getMessage(), 500);
    }
}

/**
 * Create tables if missing.
 * USERS:
 *   user_id (PK), daily_usage_mb, premium_active, referrals, referrer, ads_watched
 * PAYMENTS:
 *   invoice_id (PK), user_id, plan_code, days, amount, currency, status, raw_payload, created_at, processed_at
 */
function bootstrap_schema(PDO $pdo, $users, $payments, $is_sqlite) {
    logline('info', 'db.bootstrap.begin', ['sqlite'=>$is_sqlite]);
    if ($is_sqlite) {
        $pdo->exec("
            CREATE TABLE IF NOT EXISTS {$users}(
                user_id        INTEGER PRIMARY KEY,
                daily_usage_mb INTEGER NOT NULL DEFAULT 0,
                premium_active INTEGER NOT NULL DEFAULT 0,
                referrals      INTEGER NOT NULL DEFAULT 0,
                referrer       INTEGER NOT NULL DEFAULT 0,
                ads_watched    INTEGER NOT NULL DEFAULT 0
            );
        ");
        $pdo->exec("
            CREATE TABLE IF NOT EXISTS {$payments}(
                invoice_id   TEXT PRIMARY KEY,
                user_id      INTEGER NOT NULL,
                plan_code    TEXT,
                days         INTEGER NOT NULL,
                amount       REAL,
                currency     TEXT,
                status       TEXT,
                raw_payload  TEXT,
                created_at   INTEGER,
                processed_at INTEGER
            );
        ");
    } else {
        $pdo->exec("
            CREATE TABLE IF NOT EXISTS {$users}(
                user_id        BIGINT PRIMARY KEY,
                daily_usage_mb INT NOT NULL DEFAULT 0,
                premium_active INT NOT NULL DEFAULT 0,
                referrals      INT NOT NULL DEFAULT 0,
                referrer       BIGINT NOT NULL DEFAULT 0,
                ads_watched    INT NOT NULL DEFAULT 0
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        ");
        $pdo->exec("
            CREATE TABLE IF NOT EXISTS {$payments}(
                invoice_id   VARCHAR(128) PRIMARY KEY,
                user_id      BIGINT NOT NULL,
                plan_code    VARCHAR(32),
                days         INT NOT NULL,
                amount       DECIMAL(18,8),
                currency     VARCHAR(8),
                status       VARCHAR(32),
                raw_payload  TEXT,
                created_at   BIGINT,
                processed_at BIGINT
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
        ");
    }
    logline('info', 'db.bootstrap.ok');
}

// ----- schema inspection (cached) -----
$GLOBALS['_table_columns_cache'] = [];
function table_has_column(PDO $pdo, string $table, string $column): bool {
    $cache_key = $table . '|' . $column;
    if (isset($GLOBALS['_table_columns_cache'][$cache_key])) {
        return $GLOBALS['_table_columns_cache'][$cache_key];
    }
    $driver = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME);
    $has = false;
    try {
        if ($driver === 'sqlite') {
            $st = $pdo->prepare("PRAGMA table_info($table)");
            $st->execute();
            while ($row = $st->fetch(PDO::FETCH_ASSOC)) {
                if (strcasecmp($row['name'] ?? '', $column) === 0) { $has = true; break; }
            }
        } else {
            $st = $pdo->prepare("SHOW COLUMNS FROM `$table` LIKE :col");
            $st->execute([':col'=>$column]);
            $has = (bool)$st->fetch(PDO::FETCH_ASSOC);
        }
    } catch (Throwable $e) {
        logline('warn', 'schema.inspect.error', ['table'=>$table, 'col'=>$column, 'ex'=>$e->getMessage()]);
        $has = false;
    }
    $GLOBALS['_table_columns_cache'][$cache_key] = $has;
    logline('info', 'schema.inspect', ['table'=>$table, 'col'=>$column, 'has'=>$has]);
    return $has;
}

function ensure_user(PDO $pdo, $users, int $user_id) {
    $driver = $pdo->getAttribute(PDO::ATTR_DRIVER_NAME);
    $sql = ($driver === 'sqlite')
        ? "INSERT OR IGNORE INTO {$users}(user_id) VALUES (:uid)"
        : "INSERT IGNORE INTO {$users}(user_id) VALUES (:uid)";
    logline('info','ensure_user.sql',[]);
    $stmt = $pdo->prepare($sql);
    $stmt->execute([':uid'=>$user_id]);
}

/** Usage helpers (users.daily_usage_mb) */
function usage_row(PDO $pdo, $users, int $user_id): array {
    $st = $pdo->prepare("SELECT daily_usage_mb FROM {$users} WHERE user_id=:u");
    $st->execute([':u'=>$user_id]);
    $row = $st->fetch(PDO::FETCH_ASSOC);
    return $row ?: ['daily_usage_mb'=>0];
}
function reset_usage(PDO $pdo, $users, int $user_id): void {
    $st = $pdo->prepare("UPDATE {$users} SET daily_usage_mb=0 WHERE user_id=:u");
    $st->execute([':u'=>$user_id]);
}

/** Premium helpers (users.premium_active in days) */
function add_premium_days(PDO $pdo, $users, int $user_id, int $days): int {
    if ($pdo->getAttribute(PDO::ATTR_DRIVER_NAME) === 'sqlite') {
        $pdo->prepare("UPDATE {$users} SET premium_active = COALESCE(premium_active,0) + :d WHERE user_id=:u")
            ->execute([':d'=>$days, ':u'=>$user_id]);
    } else {
        $pdo->prepare("UPDATE {$users} SET premium_active = premium_active + :d WHERE user_id=:u")
            ->execute([':d'=>$days, ':u'=>$user_id]);
    }
    $st = $pdo->prepare("SELECT premium_active FROM {$users} WHERE user_id=:u");
    $st->execute([':u'=>$user_id]);
    return (int)$st->fetchColumn();
}

function get_user_row(PDO $pdo, $users, int $user_id): array {
    $st = $pdo->prepare("SELECT user_id, daily_usage_mb, premium_active, referrals, referrer, ads_watched FROM {$users} WHERE user_id=:u");
    $st->execute([':u'=>$user_id]);
    $row = $st->fetch(PDO::FETCH_ASSOC);
    if (!$row) {
        $row = [
            'user_id'        => $user_id,
            'daily_usage_mb' => 0,
            'premium_active' => 0,
            'referrals'      => 0,
            'referrer'       => 0,
            'ads_watched'    => 0,
        ];
    } else {
        $row['user_id']        = (int)$row['user_id'];
        $row['daily_usage_mb'] = (int)$row['daily_usage_mb'];
        $row['premium_active'] = (int)$row['premium_active'];
        $row['referrals']      = (int)$row['referrals'];
        $row['referrer']       = (int)$row['referrer'];
        $row['ads_watched']    = (int)$row['ads_watched'];
    }
    return $row;
}

function payments_has(PDO $pdo, $payments, string $invoice_id): bool {
    $st = $pdo->prepare("SELECT 1 FROM {$payments} WHERE invoice_id=:i LIMIT 1");
    $st->execute([':i'=>$invoice_id]); return (bool)$st->fetchColumn();
}
function payments_insert(PDO $pdo, $payments, array $row): void {
    $sql = "INSERT INTO {$payments}(invoice_id,user_id,plan_code,days,amount,currency,status,raw_payload,created_at,processed_at)
            VALUES(:invoice_id,:user_id,:plan_code,:days,:amount,:currency,:status,:raw_payload,:created_at,:processed_at)";
    $pdo->prepare($sql)->execute($row);
}
function parse_payload_kv(string $payload): array {
    $payload = trim($payload); if ($payload==='') return [];
    $payload = str_replace(';','&',$payload);
    parse_str($payload, $out); return is_array($out)?$out:[];
}

// ---- Webhook auth helper ----
function is_trusted_ip(array $allow): bool {
    if (!$allow) return false;
    $ip = $_SERVER['REMOTE_ADDR'] ?? '';
    foreach ($allow as $t) if ($ip === $t) return true;
    return false;
}
function get_req_header($name) {
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));
    if (isset($_SERVER[$key])) return $_SERVER[$key];
    if (function_exists('getallheaders')) {
        foreach (getallheaders() as $k => $v) {
            if (strcasecmp($k, $name) === 0) return $v;
        }
    }
    return null;
}
function cryptobot_authorized($CRYPTO_PAY_TOKEN, $CRYPTO_WEBHOOK_SECRET, $WEBHOOK_AUTH_MODE, array $TRUSTED_IPS): array {
    $hdr_token  = trim((string)(get_req_header('Crypto-Pay-API-Token') ?? ''));
       $hdr_secret = trim((string)(get_req_header('X-Webhook-Secret') ?? ''));
    $qs_secret  = trim((string)($_GET['secret'] ?? ''));
    $trusted    = is_trusted_ip($TRUSTED_IPS);

    $has_hdr_token  = ($hdr_token  !== '');
    $has_hdr_secret = ($hdr_secret !== '');
    $has_qs_secret  = ($qs_secret  !== '');

    logline('info','cryptobot.auth.check',[
        'has_hdr_token'=>$has_hdr_token,'has_hdr_secret'=>$has_hdr_secret,'has_qs_secret'=>$has_qs_secret,
        'trusted_ip'=>$trusted,'ip'=>$_SERVER['REMOTE_ADDR'] ?? '','mode'=>$WEBHOOK_AUTH_MODE
    ]);

    if ($CRYPTO_PAY_TOKEN && $has_hdr_token && hash_equals($CRYPTO_PAY_TOKEN, $hdr_token)) {
        logline('info', 'cryptobot.auth.ok', ['by'=>'token','ip'=>$_SERVER['REMOTE_ADDR'] ?? '']); return [true,'token'];
    }
    if ($CRYPTO_WEBHOOK_SECRET && ($has_hdr_secret || $has_qs_secret)) {
        if (hash_equals($CRYPTO_WEBHOOK_SECRET, $hdr_secret) || hash_equals($CRYPTO_WEBHOOK_SECRET, $qs_secret)) {
            logline('info', 'cryptobot.auth.ok', ['by'=>'secret','ip'=>$_SERVER['REMOTE_ADDR'] ?? '']); return [true,'secret'];
        }
    }
    if ($trusted) { logline('info','cryptobot.auth.ok',['by'=>'ip','ip'=>$_SERVER['REMOTE_ADDR'] ?? '']); return [true,'ip']; }
    if ($WEBHOOK_AUTH_MODE === 'relaxed') { logline('warn','cryptobot.auth.relaxed',['ip'=>$_SERVER['REMOTE_ADDR'] ?? '']); return [true,'relaxed']; }

    logline('warn','cryptobot.unauthorized',[
        'has_hdr_token'=>$has_hdr_token,'has_hdr_secret'=>$has_hdr_secret,'has_qs_secret'=>$has_qs_secret,
        'ip'=>$_SERVER['REMOTE_ADDR'] ?? ''
    ]);
    return [false,'unauthorized'];
}

// ====== Forgiving invoice extractor (handles different payload shapes) ======
function extract_invoice(array $data): array {
    $candidates = [];
    if (isset($data['invoice']) && is_array($data['invoice'])) $candidates[] = ['where'=>'data.invoice','ref'=>&$data['invoice']];
    if (isset($data['result'])  && is_array($data['result']))  $candidates[] = ['where'=>'data.result','ref'=>&$data['result']];
    if (isset($data['payload']) && is_array($data['payload'])) $candidates[] = ['where'=>'data.payload','ref'=>&$data['payload']];
    $candidates[] = ['where'=>'data.root','ref'=>&$data];

    $picked = null;
    foreach ($candidates as $c) {
        $inv = $c['ref'];
        if (isset($inv['invoice_id']) || isset($inv['id']) || isset($inv['hash']) ||
            isset($inv['status']) || isset($inv['amount']) ||
            isset($inv['asset'])  || isset($inv['currency']) || isset($inv['fiat'])) {
            $picked = ['where'=>$c['where'],'data'=>$inv];
            break;
        }
    }
    if ($picked === null) $picked = ['where'=>'data.root_fallback','data'=>$data];

    $inv = $picked['data'];
    $keys = array_keys($inv);
    logline('info','cryptobot.invoice.keys',['where'=>$picked['where'],'keys'=>$keys]);

    $src = [];

    $invoice_id = '';
    foreach (['invoice_id','id','invoiceId'] as $k) {
        if (isset($inv[$k]) && $inv[$k] !== '') { $invoice_id = (string)$inv[$k]; $src['invoice_id'] = $picked['where'].'.'.$k; break; }
    }
    if ($invoice_id === '' && isset($inv['hash']) && $inv['hash'] !== '') {
        $invoice_id = (string)$inv['hash']; $src['invoice_id'] = $picked['where'].'.hash';
    }

    $status = '';
    if (isset($inv['status']) && $inv['status'] !== '') { $status = (string)$inv['status']; $src['status'] = $picked['where'].'.status'; }
    elseif (array_key_exists('paid', $inv)) { $status = $inv['paid'] ? 'paid':'unpaid'; $src['status'] = $picked['where'].'.paid'; }

    $amount = 0.0;
    foreach (['amount','amount_usd','fiat_amount','paid_amount'] as $k) {
        if (isset($inv[$k]) && is_numeric((string)$inv[$k])) { $amount = (float)$inv[$k]; $src['amount'] = $picked['where'].'.'.$k; break; }
    }

    $currency = '';
    foreach (['currency','asset','fiat','asset_code','paid_asset'] as $k) {
        if (isset($inv[$k]) && $inv[$k] !== '') { $currency = (string)$inv[$k]; $src['currency'] = $picked['where'].'.'.$k; break; }
    }

    $payload = '';
    if (isset($inv['payload']) && is_string($inv['payload']) && $inv['payload'] !== '') {
        $payload = (string)$inv['payload']; $src['payload'] = $picked['where'].'.payload';
    } elseif (isset($data['payload']) && is_string($data['payload']) && $data['payload'] !== '') {
        $payload = (string)$data['payload']; $src['payload'] = 'data.payload';
    }

    $created_at = null;
    foreach (['created_at','paid_at','date','request_date'] as $k) {
        if (isset($inv[$k]) && $inv[$k] !== '') {
            $ts = strtotime((string)$inv[$k]);
            if ($ts) { $created_at = $ts; $src['created_at'] = $picked['where'].'.'.$k; break; }
        }
    }
    if ($created_at === null && isset($data['request_date'])) {
        $ts = strtotime((string)$data['request_date']); if ($ts) { $created_at = $ts; $src['created_at'] = 'data.request_date'; }
    }
    if ($created_at === null) $created_at = time();

    logline('info','cryptobot.invoice.extracted',[
        'from'=>$picked['where'],'src'=>$src,
        'invoice_id'=>$invoice_id,'status'=>$status,'amount'=>$amount,'currency'=>$currency,
        'has_payload'=>($payload!=='')
    ]);

    return [
        'invoice_id' => $invoice_id,
        'status'     => $status,
        'amount'     => $amount,
        'currency'   => $currency,
        'payload'    => $payload,
        'created_at' => $created_at,
    ];
}

// ========================= ROUTER =========================
try {
    $action = isset($_GET['action']) ? strtolower(trim((string)$_GET['action'])) : '';
    logline('info', 'request.begin', ['action'=>$action, 'method'=>$_SERVER['REQUEST_METHOD'] ?? '', 'ip'=>$_SERVER['REMOTE_ADDR'] ?? '']);
    if ($action === '') bad_request('missing_action');

    $pdo = pdo_connect($DB_DRIVER, $SQLITE_PATH, $MYSQL_HOST, $MYSQL_DB, $MYSQL_USER, $MYSQL_PASS, $MYSQL_CHARSET);
    bootstrap_schema($pdo, $TBL_USERS, $TBL_PAYMENTS, $IS_SQLITE);

    if ($action === 'ping') {
        json_out('ok', ['pong'=>true, 'time'=>time()]);
    }

    // ---- Crypto Bot Webhook ----
    if ($action === 'cryptobot') {
        list($ok, $how) = cryptobot_authorized($CRYPTO_PAY_TOKEN, $CRYPTO_WEBHOOK_SECRET, $WEBHOOK_AUTH_MODE, $TRUSTED_IPS);
        if (!$ok) bad_request('unauthorized_webhook', 401);

        $raw = file_get_contents('php://input');
        if (!$raw) bad_request('empty_body', 400);
        logline('info','cryptobot.payload.recv', ['bytes'=>strlen($raw), 'preview'=>substr($raw,0,400)]);

        $data = json_decode($raw, true);
        if (!is_array($data)) bad_request('invalid_json', 400);
        logline('info','cryptobot.payload.parsed', ['update_type'=>$data['update_type'] ?? null, 'keys'=>array_keys($data)]);

        $update_type = $data['update_type'] ?? '';
        if ($update_type !== 'invoice_paid') {
            json_out('ok', ['ignored'=>true, 'reason'=>'not_invoice_paid', 'auth'=>$how]);
        }

        $inv = extract_invoice($data);
        if ($inv['invoice_id'] === '') bad_request('missing_invoice_id', 422);

        if (payments_has($pdo, $TBL_PAYMENTS, $inv['invoice_id'])) {
            json_out('ok', ['duplicate'=>true, 'auth'=>$how]);
        }

        if (strtolower($inv['status']) !== 'paid') {
            payments_insert($pdo, $TBL_PAYMENTS, [
                ':invoice_id'=>$inv['invoice_id'], ':user_id'=>0, ':plan_code'=>'', ':days'=>0,
                ':amount'=>$inv['amount'], ':currency'=>$inv['currency'], ':status'=>$inv['status'],
                ':raw_payload'=>$inv['payload'], ':created_at'=>$inv['created_at'], ':processed_at'=>null,
            ]);
            json_out('ok', ['stored'=>true, 'status'=>$inv['status'], 'auth'=>$how]);
        }

        $kv   = parse_payload_kv($inv['payload']);
        logline('info','cryptobot.payload.kv', $kv);
        $uid  = isset($kv['uid'])  ? (int)$kv['uid']  : 0;
        $plan = isset($kv['plan']) ? (string)$kv['plan'] : '';
        $days = isset($kv['days']) ? (int)$kv['days'] : 0;

        if ($uid<=0 || $days<=0) {
            payments_insert($pdo, $TBL_PAYMENTS, [
                ':invoice_id'=>$inv['invoice_id'], ':user_id'=>$uid, ':plan_code'=>$plan, ':days'=>$days,
                ':amount'=>$inv['amount'], ':currency'=>$inv['currency'], ':status'=>'paid_malformed_payload',
                ':raw_payload'=>$inv['payload'], ':created_at'=>$inv['created_at'], ':processed_at'=>null,
            ]);
            bad_request('paid_but_payload_invalid', 422);
        }

        ensure_user($pdo, $TBL_USERS, $uid);
        $new_premium_days = add_premium_days($pdo, $TBL_USERS, $uid, $days);

        payments_insert($pdo, $TBL_PAYMENTS, [
            ':invoice_id'=>$inv['invoice_id'], ':user_id'=>$uid, ':plan_code'=>$plan, ':days'=>$days,
            ':amount'=>$inv['amount'], ':currency'=>$inv['currency'], ':status'=>'paid_applied',
            ':raw_payload'=>$inv['payload'], ':created_at'=>$inv['created_at'], ':processed_at'=>time(),
        ]);

        logline('info', 'cryptobot.applied', [
            'uid'=>$uid, 'days_added'=>$days, 'premium_active_now'=>$new_premium_days, 'auth'=>$how,
            'amount'=>$inv['amount'], 'currency'=>$inv['currency'], 'invoice_id'=>$inv['invoice_id']
        ]);

        // ---------- Dynamic sampleBuyReceipt (English) ----------
        $amountPretty = is_numeric($inv['amount']) ? number_format((float)$inv['amount'], 2) : (string)$inv['amount'];
        $datePretty   = date('Y-m-d H:i:s', $inv['created_at'] ?: time());
        $sampleBuyReceipt =
"✅ Payment Successful

Purchase Details:
• Your ID: {$uid}
• Transaction ID: {$inv['invoice_id']}
• Plan: {$plan}
• Subscription Duration: {$days} day(s)
• Amount Paid: {$amountPretty} {$inv['currency']}
• Transaction Time: {$datePretty}
• Bot: {$BOT_USERNAME}

🎖 Your premium subscription is now active.
• Total premium days on your account: {$new_premium_days}";

        // Send receipt (do not fail webhook if Telegram send fails)
        sendMessage($sampleBuyReceipt, null, $uid, 'HTML');

        // Final API response
        json_out('ok', [
            'applied'         => true,
            'user_id'         => $uid,
            'added_days'      => $days,
            'premium_active'  => $new_premium_days,
            'invoice_id'      => $inv['invoice_id'],
            'amount'          => $inv['amount'],
            'currency'        => $inv['currency'],
            'auth'            => $how
        ]);
    }

    // ---- Authenticated admin-ish actions ----
    if (in_array($action, ['resetusage','upgradeuser','getusage','getuser'], true)) {
        require_key($API_KEY);
    }

    $id = isset($_GET['id']) ? (int)$_GET['id'] : 0;
    if (in_array($action, ['resetusage','upgradeuser','getusage','getuser'], true)) {
        if ($id<=0) bad_request('invalid_id');
        ensure_user($pdo, $TBL_USERS, $id);
    }

    switch ($action) {
        case 'resetusage':
            reset_usage($pdo, $TBL_USERS, $id);
            $row = usage_row($pdo, $TBL_USERS, $id);
            json_out('ok', ['action'=>$action,'user_id'=>$id,'daily_usage_mb'=>(int)$row['daily_usage_mb'],'message'=>'usage_reset_to_zero']);

        case 'upgradeuser':
            $days = isset($_GET['days']) ? (int)$_GET['days'] : 0;
            if ($days<=0) bad_request('invalid_days');
            $new_premium = add_premium_days($pdo, $TBL_USERS, $id, $days);
            json_out('ok', ['action'=>$action,'user_id'=>$id,'added_days'=>$days,'premium_active'=>$new_premium]);

        case 'getusage':
            $row = usage_row($pdo, $TBL_USERS, $id);
            json_out('ok', ['action'=>$action,'user_id'=>$id,'daily_usage_mb'=>(int)$row['daily_usage_mb']]);

        case 'getuser':
            $row = get_user_row($pdo, $TBL_USERS, $id);
            json_out('ok', ['action'=>$action,'user'=>$row]);

        default:
            bad_request('unknown_action');
    }

} catch (Throwable $e) {
    logline('error', 'unhandled', ['ex'=>$e->getMessage()]);
    bad_request('unhandled: ' . $e->getMessage(), 500);
}
