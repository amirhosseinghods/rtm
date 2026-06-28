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
      CURLOPT_CONNECTTIMEOUT => 6,
      CURLOPT_TIMEOUT        => 9,
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
  $ctx = stream_context_create(['http' => ['timeout' => 9, 'header' => "User-Agent: rtm-proxy\r\n", 'ignore_errors' => true]]);
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

/* ---------- providers: each returns normalised klines (Binance shape) or null ---------- */
function p_binance($symbol, $interval, $limit, &$code, &$err) {
  foreach (['https://data-api.binance.vision','https://api.binance.com','https://api-gcp.binance.com'] as $h) {
    $b = http_get("$h/api/v3/klines?symbol=$symbol&interval=$interval&limit=$limit", $code, $err);
    if (ok($b, $code) && isset($b[0]) && $b[0] === '[') return $b;   // already Binance shape
  }
  return null;
}
function p_bybit($symbol, $ivKey, $limit, &$code, &$err) {
  global $IV;
  $iv = $IV['bybit'][$ivKey];
  $b = http_get("https://api.bybit.com/v5/market/kline?category=spot&symbol=$symbol&interval=$iv&limit=" . min(1000,$limit), $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true);
  $list = $j['result']['list'] ?? null;
  if (!$list) return null;
  $rows = [];                                  // Bybit: newest-first [start,o,h,l,c,vol,turnover]
  foreach (array_reverse($list) as $r) $rows[] = [(int)$r[0], $r[1], $r[2], $r[3], $r[4], $r[5] ?? "0"];
  return json_encode($rows);
}
function p_okx($dash, $ivKey, $limit, &$code, &$err) {
  global $IV;
  $iv = $IV['okx'][$ivKey];
  $b = http_get("https://www.okx.com/api/v5/market/candles?instId=$dash&bar=$iv&limit=" . min(300,$limit), $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true);
  $data = $j['data'] ?? null;
  if (!$data) return null;
  $rows = [];                                  // OKX: newest-first [ts,o,h,l,c,vol,...]
  foreach (array_reverse($data) as $r) $rows[] = [(int)$r[0], $r[1], $r[2], $r[3], $r[4], $r[5] ?? "0"];
  return json_encode($rows);
}
function p_kucoin($dash, $ivKey, $limit, &$code, &$err) {
  global $IV;
  $iv = $IV['kucoin'][$ivKey];
  $b = http_get("https://api.kucoin.com/api/v1/market/candles?type=$iv&symbol=$dash", $code, $err);
  if (!ok($b, $code)) return null;
  $j = json_decode($b, true);
  $data = $j['data'] ?? null;
  if (!$data) return null;
  $rows = [];                                  // KuCoin: newest-first [time(s),open,close,high,low,vol,turnover]
  foreach (array_reverse($data) as $r) $rows[] = [((int)$r[0]) * 1000, $r[1], $r[3], $r[4], $r[2], $r[5] ?? "0"];
  $rows = array_slice($rows, -$limit);
  return json_encode($rows);
}

/* ---------- ticker providers: each returns a price string or null ---------- */
function t_binance($symbol, &$code, &$err) {
  foreach (['https://data-api.binance.vision','https://api.binance.com'] as $h) {
    $b = http_get("$h/api/v3/ticker/price?symbol=$symbol", $code, $err);
    if (ok($b, $code)) { $j = json_decode($b, true); if (isset($j['price'])) return (string)$j['price']; }
  }
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

/* ---------- klines ---------- */
if ($path === 'klines') {
  $r = p_binance($symbol, $interval, $limit, $c, $e); if ($r !== null) { header('X-RTM-Source: binance'); echo $r; exit; }
  $r = p_bybit($symbol, $interval, $limit, $c, $e);   if ($r !== null) { header('X-RTM-Source: bybit');   echo $r; exit; }
  $r = p_okx($dash, $interval, $limit, $c, $e);       if ($r !== null) { header('X-RTM-Source: okx');     echo $r; exit; }
  $r = p_kucoin($dash, $interval, $limit, $c, $e);    if ($r !== null) { header('X-RTM-Source: kucoin');  echo $r; exit; }
  http_response_code(502); echo json_encode(['__error' => 'upstream unreachable']); exit;
}

/* ---------- ticker ---------- */
$p = t_binance($symbol, $c, $e); if ($p !== null) { header('X-RTM-Source: binance'); echo json_encode(['price' => $p]); exit; }
$p = t_bybit($symbol, $c, $e);   if ($p !== null) { header('X-RTM-Source: bybit');   echo json_encode(['price' => $p]); exit; }
$p = t_okx($dash, $c, $e);       if ($p !== null) { header('X-RTM-Source: okx');     echo json_encode(['price' => $p]); exit; }
$p = t_kucoin($dash, $c, $e);    if ($p !== null) { header('X-RTM-Source: kucoin');  echo json_encode(['price' => $p]); exit; }
http_response_code(502); echo json_encode(['__error' => 'upstream unreachable']);
