<?php
/**
 * Market-data proxy (host-side), multi-exchange with Binance-shaped output.
 *
 * The chart's candles/price are normally fetched straight from the visitor's browser to
 * api.binance.com, which is geo-blocked in some regions (e.g. Iran) -> empty chart. This
 * proxy fetches from the SERVER instead. If the server ALSO can't reach Binance (its IP is
 * blocked too), it falls back to other public exchanges (Bybit / OKX / KuCoin) and NORMALISES
 * their response into Binance's shape, so app.js stays unchanged.
 *
 *   proxy.php?path=klines&symbol=BTCUSDT&interval=5m&limit=1000
 *   proxy.php?path=ticker&symbol=BTCUSDT
 *   proxy.php?path=diag&symbol=BTCUSDT   -> reports which sources the server can reach
 *
 * klines output: Binance shape [[openMs,"o","h","l","c",...], ...]  (app.js reads k[0..4])
 * ticker output: {"price":"123.45"}
 */
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: public, max-age=5');

$path   = $_GET['path'] ?? '';
$symbol = strtoupper(preg_replace('/[^A-Za-z0-9]/', '', $_GET['symbol'] ?? ''));
if ($symbol === '' || !in_array($path, ['klines', 'ticker', 'diag'], true)) {
  http_response_code(400); echo json_encode(['__error' => 'bad request']); exit;
}
// split BTCUSDT -> base BTC / quote USDT (all crypto symbols here end in USDT)
$quote = preg_match('/(USDT|USDC|USD)$/', $symbol, $mq) ? $mq[1] : 'USDT';
$base  = preg_replace('/' . $quote . '$/', '', $symbol);
$dash  = $base . '-' . $quote;   // OKX / KuCoin instrument id

$allowedIv = ['1m','5m','15m','30m','1h','4h','1d'];
$interval  = in_array($_GET['interval'] ?? '', $allowedIv, true) ? $_GET['interval'] : '5m';
$limit     = max(1, min(1000, (int)($_GET['limit'] ?? 1000)));

/* ---------- low-level HTTP (curl, with file_get_contents fallback) ---------- */
function http_get($url, &$code = null, &$err = null) {
  $code = 0; $err = '';
  if (function_exists('curl_init')) {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
      CURLOPT_RETURNTRANSFER => true,
      CURLOPT_CONNECTTIMEOUT => 3,
      CURLOPT_TIMEOUT        => 6,
      CURLOPT_FOLLOWLOCATION => true,
      CURLOPT_SSL_VERIFYPEER => false,   // some shared hosts have a stale CA bundle
      CURLOPT_USERAGENT      => 'Mozilla/5.0 rtm-proxy',
    ]);
    $body = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    if ($body === false) $err = curl_error($ch);
    curl_close($ch);
    return ($body === false) ? null : $body;
  }
  $ctx = stream_context_create(['http' => ['timeout' => 6, 'header' => "User-Agent: rtm-proxy\r\n", 'ignore_errors' => true]]);
  $body = @file_get_contents($url, false, $ctx);
  if (isset($http_response_header[0]) && preg_match('/\s(\d{3})\s/', $http_response_header[0], $m)) $code = (int)$m[1];
  return ($body === false) ? null : $body;
}
function ok($body, $code) { return $body !== null && $body !== '' && $code >= 200 && $code < 300; }

/* ---------- interval maps ---------- */
$IV = [
  'bybit'  => ['1m'=>'1','5m'=>'5','15m'=>'15','30m'=>'30','1h'=>'60','4h'=>'240','1d'=>'D'],
  'okx'    => ['1m'=>'1m','5m'=>'5m','15m'=>'15m','30m'=>'30m','1h'=>'1H','4h'=>'4H','1d'=>'1D'],
  'kucoin' => ['1m'=>'1min','5m'=>'5min','15m'=>'15min','30m'=>'30min','1h'=>'1hour','4h'=>'4hour','1d'=>'1day'],
];
$TFSEC = ['1m'=>60,'5m'=>300,'15m'=>900,'30m'=>1800,'1h'=>3600,'4h'=>14400,'1d'=>86400];

/* ---------- providers: each returns ONE page of klines as ASC PHP rows [tsMs,o,h,l,c,v]
   (or null). `$end` (ms, 0 = latest) is the pagination cursor for deep history. ---------- */
function p_binance($symbol, $interval, $limit, $end, &$code, &$err) {
  // only data-api.binance.vision (the public market-data host): it returns 451 instantly when
  // blocked, so no slow timeout. If it's reachable, the rest of Binance is too.
  $u = "https://data-api.binance.vision/api/v3/klines?symbol=$symbol&interval=$interval&limit=" . min(1000, $limit);
  if ($end) $u .= "&endTime=" . $end;
  $b = http_get($u, $code, $err);
  if (!ok($b, $code) || !isset($b[0]) || $b[0] !== '[') return null;
  $j = json_decode($b, true); if (!is_array($j)) return null;
  $rows = [];                                  // Binance: already oldest-first [openTime,o,h,l,c,vol,...]
  foreach ($j as $r) $rows[] = [(int)$r[0], $r[1], $r[2], $r[3], $r[4], $r[5] ?? "0"];
  return $rows;
}
function p_bybit($symbol, $ivKey, $limit, $end, &$code, &$err) {
  global $IV;
  $iv = $IV['bybit'][$ivKey];
  $u = "https://api.bybit.com/v5/market/kline?category=spot&symbol=$symbol&interval=$iv&limit=" . min(1000, $limit);
  if ($end) $u .= "&end=" . $end;
  $b = http_get($u, $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true);
  $list = $j['result']['list'] ?? null;
  if (!$list) return null;
  $rows = [];                                  // Bybit: newest-first [start,o,h,l,c,vol,turnover] -> reverse to ASC
  foreach (array_reverse($list) as $r) $rows[] = [(int)$r[0], $r[1], $r[2], $r[3], $r[4], $r[5] ?? "0"];
  return $rows;
}
function p_okx($dash, $ivKey, $limit, $end, &$code, &$err) {
  global $IV;
  $iv = $IV['okx'][$ivKey];
  $u = "https://www.okx.com/api/v5/market/candles?instId=$dash&bar=$iv&limit=" . min(300, $limit);
  if ($end) $u .= "&after=" . $end;            // OKX 'after' = records older than this ts
  $b = http_get($u, $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true);
  $data = $j['data'] ?? null;
  if (!$data) return null;
  $rows = [];                                  // OKX: newest-first [ts,o,h,l,c,vol,...] -> reverse to ASC
  foreach (array_reverse($data) as $r) $rows[] = [(int)$r[0], $r[1], $r[2], $r[3], $r[4], $r[5] ?? "0"];
  return $rows;
}
function p_kucoin($dash, $ivKey, $limit, $end, &$code, &$err) {
  global $IV, $TFSEC;
  $iv = $IV['kucoin'][$ivKey];
  $u = "https://api.kucoin.com/api/v1/market/candles?type=$iv&symbol=$dash";
  if ($end) {                                  // KuCoin paginates by startAt/endAt (seconds)
    $endSec = intdiv($end, 1000);
    $u .= "&endAt=$endSec&startAt=" . ($endSec - min(1500, $limit) * $TFSEC[$ivKey]);
  }
  $b = http_get($u, $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true);
  $data = $j['data'] ?? null;
  if (!$data) return null;
  $rows = [];                                  // KuCoin: newest-first [time(s),open,close,high,low,...] -> ASC
  foreach (array_reverse($data) as $r) $rows[] = [((int)$r[0]) * 1000, $r[1], $r[3], $r[4], $r[2], $r[5] ?? "0"];
  return $rows;
}

/* ---------- ticker providers: each returns a price string or null ---------- */
function t_binance($symbol, &$code, &$err) {
  $b = http_get("https://data-api.binance.vision/api/v3/ticker/price?symbol=$symbol", $code, $err);
  if (ok($b, $code)) { $j = json_decode($b, true); if (isset($j['price'])) return (string)$j['price']; }
  return null;
}
function t_bybit($symbol, &$code, &$err) {
  $b = http_get("https://api.bybit.com/v5/market/tickers?category=spot&symbol=$symbol", $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true); return $j['result']['list'][0]['lastPrice'] ?? null;
}
function t_okx($dash, &$code, &$err) {
  $b = http_get("https://www.okx.com/api/v5/market/ticker?instId=$dash", $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true); return $j['data'][0]['last'] ?? null;
}
function t_kucoin($dash, &$code, &$err) {
  $b = http_get("https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=$dash", $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true); return $j['data']['price'] ?? null;
}

/* ---------- remember the last working exchange so we skip dead-host timeouts ---------- */
function cache_dir() { $d = sys_get_temp_dir(); return ($d && is_dir($d) && is_writable($d)) ? $d : __DIR__ . '/data'; }
function src_get($key) {
  $f = cache_dir() . "/rtm_src_$key";
  if (is_file($f) && (time() - filemtime($f) < 600)) { $v = trim((string)@file_get_contents($f)); return $v !== '' ? $v : null; }
  return null;
}
function src_set($key, $name) { @file_put_contents(cache_dir() . "/rtm_src_$key", $name); }
function order_with_cache($key) {                       // cached winner first, then the rest
  $all = ['binance', 'bybit', 'okx', 'kucoin'];
  $hit = src_get($key);
  if ($hit) { array_unshift($all, $hit); $all = array_values(array_unique($all)); }
  return $all;
}
function run_klines($name, $symbol, $dash, $interval, $limit, $end, &$c, &$e) {
  if ($name === 'binance') return p_binance($symbol, $interval, $limit, $end, $c, $e);
  if ($name === 'bybit')   return p_bybit($symbol, $interval, $limit, $end, $c, $e);
  if ($name === 'okx')     return p_okx($dash, $interval, $limit, $end, $c, $e);
  if ($name === 'kucoin')  return p_kucoin($dash, $interval, $limit, $end, $c, $e);
  return null;
}
function run_ticker($name, $symbol, $dash, &$c, &$e) {
  if ($name === 'binance') return t_binance($symbol, $c, $e);
  if ($name === 'bybit')   return t_bybit($symbol, $c, $e);
  if ($name === 'okx')     return t_okx($dash, $c, $e);
  if ($name === 'kucoin')  return t_kucoin($dash, $c, $e);
  return null;
}

/* ---------- diag: show what the server can actually reach ---------- */
if ($path === 'diag') {
  $out = [];
  foreach ([
    'binance'      => "https://data-api.binance.vision/api/v3/ticker/price?symbol=$symbol",
    'binance-main' => "https://api.binance.com/api/v3/ticker/price?symbol=$symbol",
    'bybit'        => "https://api.bybit.com/v5/market/tickers?category=spot&symbol=$symbol",
    'okx'          => "https://www.okx.com/api/v5/market/ticker?instId=$dash",
    'kucoin'       => "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=$dash",
    'github'       => "https://github.com",
  ] as $name => $url) {
    $b = http_get($url, $c, $e);
    $out[$name] = ['http' => $c, 'err' => $e, 'len' => $b === null ? 0 : strlen($b), 'sample' => $b === null ? null : substr($b, 0, 80)];
  }
  echo json_encode(['curl' => function_exists('curl_init'), 'symbol' => $symbol, 'instId' => $dash, 'results' => $out], JSON_PRETTY_PRINT | JSON_UNESCAPED_SLASHES);
  exit;
}

/* ---------- klines (single page, or deep history paginated backward) ---------- */
if ($path === 'klines') {
  $deep = !empty($_GET['deep']);
  $want = $deep ? min(6000, max($limit, 1000)) : $limit;
  foreach (order_with_cache('klines') as $name) {
    $pageMax = ($name === 'okx') ? 300 : 1000;
    $byTs = []; $end = 0; $guard = 0;
    do {
      $page = run_klines($name, $symbol, $dash, $interval, $pageMax, $end, $c, $e);
      if ($page === null || !count($page)) break;
      foreach ($page as $row) $byTs[$row[0]] = $row;     // dedup by timestamp
      if (count($page) < $pageMax) break;                // source has no older data
      $end = $page[0][0] - 1;                            // page is ASC -> oldest bar is [0]
    } while ($deep && count($byTs) < $want && ++$guard < 12);
    if ($byTs) {
      src_set('klines', $name);
      ksort($byTs);                                       // chronological
      $rows = array_values($byTs);
      if (count($rows) > $want) $rows = array_slice($rows, -$want);
      header("X-RTM-Source: $name");
      if ($deep) header('Cache-Control: public, max-age=300');   // deep history barely changes
      echo json_encode($rows); exit;
    }
  }
  http_response_code(502); echo json_encode(['__error' => 'upstream unreachable']); exit;
}

/* ---------- ticker ---------- */
foreach (order_with_cache('ticker') as $name) {
  $p = run_ticker($name, $symbol, $dash, $c, $e);
  if ($p !== null) { src_set('ticker', $name); header("X-RTM-Source: $name"); echo json_encode(['price' => $p]); exit; }
}
http_response_code(502); echo json_encode(['__error' => 'upstream unreachable']);
