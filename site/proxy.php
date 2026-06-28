<?php
/**
 * Binance market-data proxy (host-side).
 *
 * The chart's candles/price are normally fetched straight from the visitor's browser to
 * api.binance.com. That domain is geo-blocked for many regions (e.g. Iran), so the fetch
 * fails and the chart renders empty. This proxy makes the request from the SERVER instead
 * (whose IP is not blocked) and relays Binance's JSON back unchanged.
 *
 * Endpoints (kept identical in shape to Binance so app.js parsing is unchanged):
 *   proxy.php?path=klines&symbol=BTCUSDT&interval=5m&limit=1000
 *   proxy.php?path=ticker&symbol=BTCUSDT
 */
header('Content-Type: application/json; charset=utf-8');
header('Access-Control-Allow-Origin: *');
header('Cache-Control: public, max-age=5');

// ---- validate inputs (never forward raw user input into the upstream URL) ----
$path = $_GET['path'] ?? '';
$symbol = strtoupper(preg_replace('/[^A-Za-z0-9]/', '', $_GET['symbol'] ?? ''));
if ($symbol === '' || !in_array($path, ['klines', 'ticker'], true)) {
  http_response_code(400); echo json_encode(['__error' => 'bad request']); exit;
}

if ($path === 'klines') {
  $allowedIv = ['1m','5m','15m','30m','1h','4h','1d'];
  $interval = in_array($_GET['interval'] ?? '', $allowedIv, true) ? $_GET['interval'] : '5m';
  $limit = max(1, min(1000, (int)($_GET['limit'] ?? 1000)));
  $upPath = "/api/v3/klines?symbol={$symbol}&interval={$interval}&limit={$limit}";
} else { // ticker
  $upPath = "/api/v3/ticker/price?symbol={$symbol}";
}

// ---- try several Binance hosts; data-api.binance.vision often works where the main API is blocked ----
$hosts = [
  'https://data-api.binance.vision',
  'https://api.binance.com',
  'https://api-gcp.binance.com',
  'https://api1.binance.com',
];

function fetch_upstream($url) {
  if (function_exists('curl_init')) {
    $ch = curl_init($url);
    curl_setopt_array($ch, [
      CURLOPT_RETURNTRANSFER => true,
      CURLOPT_CONNECTTIMEOUT => 6,
      CURLOPT_TIMEOUT => 9,
      CURLOPT_FOLLOWLOCATION => true,
      CURLOPT_SSL_VERIFYPEER => true,
      CURLOPT_USERAGENT => 'rtm-proxy/1.0',
    ]);
    $body = curl_exec($ch);
    $code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($body !== false && $code >= 200 && $code < 300) return $body;
    return null;
  }
  // fallback if curl is unavailable
  $ctx = stream_context_create(['http' => ['timeout' => 9, 'header' => "User-Agent: rtm-proxy/1.0\r\n"]]);
  $body = @file_get_contents($url, false, $ctx);
  return $body === false ? null : $body;
}

foreach ($hosts as $h) {
  $body = fetch_upstream($h . $upPath);
  if ($body !== null && $body !== '' && $body[0] !== '{' /* {"code":...} = Binance error */) {
    echo $body; exit;
  }
  // a JSON object that is NOT an error could still be valid for ticker; accept it
  if ($body !== null && $body !== '' && strpos($body, '"code"') === false && strpos($body, '"msg"') === false) {
    echo $body; exit;
  }
}

http_response_code(502);
echo json_encode(['__error' => 'upstream unreachable']);
