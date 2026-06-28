<?php
/**
 * Manual trade journal (host-side, MySQL), now SESSION-SCOPED per user. Each user sees only
 * their own WIN/LOSS log; an admin may pass ?user=<name> to view any user's journal. The
 * auto-learning runs in GitHub Actions; this is the user's own record. Reads DB creds from
 * config.local.php (written by installer). Nothing here places orders.
 */
require_once __DIR__ . '/auth.php';
require_login();                                  // 401 JSON if no session
header('Content-Type: application/json; charset=utf-8');

$m = rtm_db();
if (!$m) { http_response_code(500); echo json_encode(['entries' => [], 'error' => 'db']); exit; }
$m->query("CREATE TABLE IF NOT EXISTS rtm_journal (
  id INT AUTO_INCREMENT PRIMARY KEY, ts DATETIME DEFAULT CURRENT_TIMESTAMP,
  symbol VARCHAR(24), tf VARCHAR(8), dir VARCHAR(8), src VARCHAR(24), confidence VARCHAR(8),
  entry DOUBLE, sl DOUBLE, tp2 DOUBLE, status VARCHAR(12) DEFAULT 'OPEN', outcome VARCHAR(8) NULL,
  user_id VARCHAR(64) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

$me = current_user();
$scope = $me['u'];
if (is_admin() && !empty($_GET['user'])) $scope = preg_replace('/[^A-Za-z0-9_]/', '', $_GET['user']);

$body = json_decode(file_get_contents('php://input'), true) ?: [];
$action = $body['action'] ?? ($_SERVER['REQUEST_METHOD'] === 'POST' ? 'log' : 'get');

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  if (!csrf_check(csrf_from_request())) { http_response_code(400); echo json_encode(['ok' => false, 'error' => 'csrf']); exit; }

  if ($action === 'log') {
    $z = $body['setup'] ?? [];
    if (!$z) { echo json_encode(['ok' => false, 'error' => 'no setup']); exit; }
    $st = $m->prepare("INSERT INTO rtm_journal (user_id,symbol,tf,dir,src,confidence,entry,sl,tp2) VALUES (?,?,?,?,?,?,?,?,?)");
    $sym = $body['symbol'] ?? ''; $tf = $body['tf'] ?? ''; $dir = $z['dir'] ?? ''; $src = $z['src'] ?? '';
    $conf = $z['confidence'] ?? ''; $en = $z['entry'] ?? 0; $sl = $z['sl'] ?? 0; $tp = $z['tp2'] ?? 0;
    $st->bind_param('ssssssddd', $me['u'], $sym, $tf, $dir, $src, $conf, $en, $sl, $tp); $st->execute();
    echo json_encode(['ok' => true, 'id' => $m->insert_id]); exit;
  }
  if ($action === 'outcome') {
    $id = (int)($body['id'] ?? 0); $oc = $body['outcome'] ?? '';
    // a user can only close their OWN trade; admin can close within the scoped user
    $st = $m->prepare("UPDATE rtm_journal SET outcome=?, status='CLOSED' WHERE id=? AND user_id=?");
    $st->bind_param('sis', $oc, $id, $scope); $st->execute();
    echo json_encode(['ok' => true]); exit;
  }
}

// GET: this user's entries + simple learn summary
$rows = [];
$st = $m->prepare("SELECT * FROM rtm_journal WHERE user_id=? ORDER BY id DESC LIMIT 200");
$st->bind_param('s', $scope); $st->execute();
$r = $st->get_result();
while ($x = $r->fetch_assoc()) { $rows[] = $x; }
$closed = 0; $win = 0;
foreach ($rows as $x) { if ($x['status'] === 'CLOSED') { $closed++; if ($x['outcome'] === 'WIN') $win++; } }
echo json_encode(['entries' => array_reverse($rows), 'user' => $scope,
  'learn' => ['overall' => ['closed' => $closed, 'win_rate' => $closed ? round($win / $closed, 3) : null, 'sim_balance' => 1000]]]);
