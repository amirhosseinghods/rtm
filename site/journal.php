<?php
/**
 * Manual trade journal (host-side, MySQL). The auto-learning runs in GitHub Actions; this is
 * the user's own WIN/LOSS log. GET returns entries + a simple win-rate; POST logs a setup or
 * records an outcome. Reads DB creds from config.local.php (written by installer).
 */
header('Content-Type: application/json; charset=utf-8');
$cfgf = __DIR__ . '/config.local.php';
if (!file_exists($cfgf)) { echo json_encode(['entries'=>[], 'error'=>'not configured']); exit; }
$cfg = require $cfgf; $d = $cfg['db'] ?? [];
$m = @new mysqli($d['dbh'] ?? 'localhost', $d['dbu'] ?? '', $d['dbp'] ?? '', $d['dbn'] ?? '');
if ($m->connect_errno) { http_response_code(500); echo json_encode(['entries'=>[], 'error'=>'db']); exit; }
$m->set_charset('utf8mb4');
$m->query("CREATE TABLE IF NOT EXISTS rtm_journal (
  id INT AUTO_INCREMENT PRIMARY KEY, ts DATETIME DEFAULT CURRENT_TIMESTAMP,
  symbol VARCHAR(24), tf VARCHAR(8), dir VARCHAR(8), src VARCHAR(24), confidence VARCHAR(8),
  entry DOUBLE, sl DOUBLE, tp2 DOUBLE, status VARCHAR(12) DEFAULT 'OPEN', outcome VARCHAR(8) NULL
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

$body = json_decode(file_get_contents('php://input'), true) ?: [];
$action = $body['action'] ?? ($_SERVER['REQUEST_METHOD']==='POST' ? 'log' : 'get');

if ($_SERVER['REQUEST_METHOD'] === 'POST') {
  if ($action === 'log') {
    $z = $body['setup'] ?? [];
    if (!$z) { echo json_encode(['ok'=>false, 'error'=>'no setup']); exit; }
    $st = $m->prepare("INSERT INTO rtm_journal (symbol,tf,dir,src,confidence,entry,sl,tp2) VALUES (?,?,?,?,?,?,?,?)");
    $sym=$body['symbol']??''; $tf=$body['tf']??''; $dir=$z['dir']??''; $src=$z['src']??'';
    $conf=$z['confidence']??''; $en=$z['entry']??0; $sl=$z['sl']??0; $tp=$z['tp2']??0;
    $st->bind_param('sssssddd',$sym,$tf,$dir,$src,$conf,$en,$sl,$tp); $st->execute();
    echo json_encode(['ok'=>true, 'id'=>$m->insert_id]); exit;
  }
  if ($action === 'outcome') {
    $id=(int)($body['id']??0); $oc=$body['outcome']??'';
    $st=$m->prepare("UPDATE rtm_journal SET outcome=?, status='CLOSED' WHERE id=?");
    $st->bind_param('si',$oc,$id); $st->execute();
    echo json_encode(['ok'=>true]); exit;
  }
}

// GET: entries + simple learn summary
$rows=[]; $r=$m->query("SELECT * FROM rtm_journal ORDER BY id DESC LIMIT 200");
while($x=$r->fetch_assoc()){ $x['ts']=$x['ts']; $rows[]=$x; }
$closed=0;$win=0; foreach($rows as $x){ if($x['status']==='CLOSED'){ $closed++; if($x['outcome']==='WIN')$win++; } }
echo json_encode(['entries'=>array_reverse($rows),
  'learn'=>['overall'=>['closed'=>$closed,'win_rate'=>$closed?round($win/$closed,3):null,'sim_balance'=>1000]]]);
