<?php
/**
 * Per-user chart drawings (trendlines, rectangles, fib, rays, text). Session-scoped — each
 * user only ever reads/writes their OWN drawings, keyed by (user, symbol, tf).
 *   GET  ?symbol=&tf=                      -> {drawings:[{id,type,data}]}
 *   POST {action:save, symbol,tf,type,data}-> {ok,id}
 *   POST {action:delete, id}               -> {ok}
 *   POST {action:clear, symbol,tf}         -> {ok}
 */
require_once __DIR__ . '/auth.php';
require_login();
header('Content-Type: application/json; charset=utf-8');

$m = rtm_db();
if (!$m) { http_response_code(500); echo json_encode(['drawings' => [], 'error' => 'db']); exit; }
$m->query("CREATE TABLE IF NOT EXISTS rtm_drawings (
  id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(64) NOT NULL,
  symbol VARCHAR(24) NOT NULL, tf VARCHAR(8) NOT NULL,
  type VARCHAR(16) NOT NULL, data TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  INDEX idx_draw (user_id, symbol, tf)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

$me = current_user();
$uid = $me['u'];

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  if (!csrf_check(csrf_from_request())) { http_response_code(400); echo json_encode(['ok' => false, 'error' => 'csrf']); exit; }
  $body = json_decode(file_get_contents('php://input'), true) ?: [];
  $action = $body['action'] ?? '';

  if ($action === 'save') {
    $sym = substr((string)($body['symbol'] ?? ''), 0, 24);
    $tf  = substr((string)($body['tf'] ?? ''), 0, 8);
    $type = substr((string)($body['type'] ?? ''), 0, 16);
    $data = json_encode($body['data'] ?? [], JSON_UNESCAPED_UNICODE);
    if ($sym === '' || $tf === '' || $type === '') { echo json_encode(['ok' => false, 'error' => 'bad']); exit; }
    if (strlen($data) > 20000) { echo json_encode(['ok' => false, 'error' => 'too_big']); exit; }
    $st = $m->prepare("INSERT INTO rtm_drawings (user_id,symbol,tf,type,data) VALUES (?,?,?,?,?)");
    $st->bind_param('sssss', $uid, $sym, $tf, $type, $data); $st->execute();
    echo json_encode(['ok' => true, 'id' => $m->insert_id]); exit;
  }
  if ($action === 'delete') {
    $id = (int)($body['id'] ?? 0);
    $st = $m->prepare("DELETE FROM rtm_drawings WHERE id=? AND user_id=?");
    $st->bind_param('is', $id, $uid); $st->execute();
    echo json_encode(['ok' => true]); exit;
  }
  if ($action === 'clear') {
    $sym = substr((string)($body['symbol'] ?? ''), 0, 24);
    $tf  = substr((string)($body['tf'] ?? ''), 0, 8);
    $st = $m->prepare("DELETE FROM rtm_drawings WHERE user_id=? AND symbol=? AND tf=?");
    $st->bind_param('sss', $uid, $sym, $tf); $st->execute();
    echo json_encode(['ok' => true]); exit;
  }
  echo json_encode(['ok' => false, 'error' => 'unknown_action']); exit;
}

// GET: this user's drawings for symbol+tf
$sym = substr((string)($_GET['symbol'] ?? ''), 0, 24);
$tf  = substr((string)($_GET['tf'] ?? ''), 0, 8);
$out = [];
$st = $m->prepare("SELECT id,type,data FROM rtm_drawings WHERE user_id=? AND symbol=? AND tf=? ORDER BY id ASC");
$st->bind_param('sss', $uid, $sym, $tf); $st->execute();
$r = $st->get_result();
while ($x = $r->fetch_assoc()) {
  $out[] = ['id' => (int)$x['id'], 'type' => $x['type'], 'data' => json_decode($x['data'], true)];
}
echo json_encode(['drawings' => $out]);
