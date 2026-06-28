<?php
/**
 * Admin/management panel. Owner-only (role=admin). Create / enable-disable / delete users,
 * see per-user journal stats and the system's learned winrate/PF/stop-rates. Read-only on the
 * engine data (site/data/*.json); nothing here executes trades.
 */
require_once __DIR__ . '/auth.php';
require_admin();
$m = rtm_db();
$me = current_user();
function flash($s){ $_SESSION['flash'] = $s; }
function back(){ header('Location: admin.php'); exit; }
function uname_ok($u){ return (bool)preg_match('/^[A-Za-z0-9_]{3,64}$/', $u); }

/* ---------- mutations (POST + CSRF) ---------- */
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
  if (!csrf_check(csrf_from_request())) { http_response_code(400); flash('نشست منقضی شد؛ دوباره تلاش کن.'); back(); }
  $act = $_POST['action'] ?? '';

  if ($act === 'create_user') {
    $u = trim($_POST['username'] ?? ''); $p = (string)($_POST['password'] ?? '');
    $email = trim($_POST['email'] ?? ''); $role = ($_POST['role'] ?? 'user') === 'admin' ? 'admin' : 'user';
    if (!uname_ok($u)) flash('نام کاربری نامعتبر است (۳ تا ۶۴ حرف، حروف/عدد/زیرخط).');
    elseif (strlen($p) < 8) flash('رمز عبور باید حداقل ۸ کاراکتر باشد.');
    else {
      $exists = $m->prepare("SELECT 1 FROM rtm_users WHERE username=?"); $exists->bind_param('s', $u); $exists->execute();
      if ($exists->get_result()->fetch_row()) flash('این نام کاربری از قبل وجود دارد.');
      else {
        $ph = password_hash($p, PASSWORD_BCRYPT);
        $st = $m->prepare("INSERT INTO rtm_users (username, pass_hash, email, role, active) VALUES (?,?,?,?,1)");
        $st->bind_param('ssss', $u, $ph, $email, $role); $st->execute();
        flash('کاربر «' . $u . '» ساخته شد.');
      }
    }
    back();
  }

  // count active admins to protect the last one
  $admins = (int)$m->query("SELECT COUNT(*) FROM rtm_users WHERE role='admin' AND active=1")->fetch_row()[0];

  if ($act === 'set_active') {
    $u = trim($_POST['username'] ?? ''); $val = (int)($_POST['active'] ?? 0) ? 1 : 0;
    if ($u === $me['u'] && !$val) flash('نمی‌توانی حسابِ خودت را غیرفعال کنی.');
    else {
      $isAdmin = $m->prepare("SELECT role FROM rtm_users WHERE username=?"); $isAdmin->bind_param('s', $u); $isAdmin->execute();
      $r = $isAdmin->get_result()->fetch_assoc();
      if ($r && $r['role'] === 'admin' && !$val && $admins <= 1) flash('این تنها ادمینِ فعال است؛ غیرفعال نشد.');
      else { $st = $m->prepare("UPDATE rtm_users SET active=? WHERE username=?"); $st->bind_param('is', $val, $u); $st->execute(); flash('وضعیتِ «' . $u . '» بروز شد.'); }
    }
    back();
  }

  if ($act === 'delete_user') {
    $u = trim($_POST['username'] ?? '');
    $isAdmin = $m->prepare("SELECT role FROM rtm_users WHERE username=?"); $isAdmin->bind_param('s', $u); $isAdmin->execute();
    $r = $isAdmin->get_result()->fetch_assoc();
    if ($u === $me['u']) flash('نمی‌توانی حسابِ خودت را حذف کنی.');
    elseif ($r && $r['role'] === 'admin' && $admins <= 1) flash('تنها ادمینِ فعال حذف نشد.');
    else {
      $st = $m->prepare("DELETE FROM rtm_users WHERE username=?"); $st->bind_param('s', $u); $st->execute();
      $st = $m->prepare("DELETE FROM rtm_journal WHERE user_id=?"); $st->bind_param('s', $u); $st->execute();
      flash('کاربر «' . $u . '» و ژورنالش حذف شد.');
    }
    back();
  }
}

/* ---------- data for the dashboard ---------- */
function h($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }
$users = [];
$r = $m->query("SELECT username, email, role, active, created_at, last_login FROM rtm_users ORDER BY role='admin' DESC, created_at ASC");
while ($x = $r->fetch_assoc()) $users[$x['username']] = $x;

// per-user journal aggregates
$pu = [];
$r = $m->query("SELECT user_id,
                 COUNT(*) total,
                 SUM(status='CLOSED') closed,
                 SUM(outcome='WIN') wins,
                 SUM(outcome='LOSS') losses
               FROM rtm_journal GROUP BY user_id");
while ($x = $r->fetch_assoc()) $pu[(string)$x['user_id']] = $x;

// system journal totals
$sysTot = ['total'=>0,'closed'=>0,'wins'=>0,'losses'=>0];
foreach ($pu as $g) { foreach ($sysTot as $k=>$_) $sysTot[$k] += (int)($g[$k] ?? 0); }
$sysWR = ($sysTot['wins']+$sysTot['losses']) ? round(100*$sysTot['wins']/($sysTot['wins']+$sysTot['losses']),1) : null;

// engine learning stats from served JSON (read-only)
$eng = ['dir_rate'=>null,'n'=>null,'by_combo'=>null];
$mf = @json_decode(@file_get_contents(__DIR__.'/data/manifest.json'), true);
if (is_array($mf) && !empty($mf['learning']['overall'])) {
  $o = $mf['learning']['overall'];
  $eng['dir_rate'] = isset($o['rate']) ? round(100*$o['rate'],1) : null;
  $eng['n'] = $o['n'] ?? null;
}
// aggregate setup_lessons across served signal files (system winrate / stop-rate / expR)
$les = ['n'=>0,'win'=>0.0,'stop'=>0.0,'exp'=>0.0,'k'=>0];
foreach (glob(__DIR__.'/data/sig_*.json') ?: [] as $f) {
  $j = @json_decode(@file_get_contents($f), true);
  $ov = $j['signal']['setup_lessons']['overall'] ?? null;
  if (is_array($ov) && !empty($ov['n'])) {
    $n = (int)$ov['n'];
    $les['n'] += $n; $les['k']++;
    $les['win']  += ($ov['win_rate']  ?? 0) * $n;
    $les['stop'] += ($ov['stop_rate'] ?? 0) * $n;
    $les['exp']  += ($ov['expR']      ?? 0) * $n;
  }
}
$sysSetupWR = $les['n'] ? round($les['win']/$les['n'],1) : null;
$sysStop    = $les['n'] ? round($les['stop']/$les['n'],1) : null;
$sysExp     = $les['n'] ? round($les['exp']/$les['n'],3) : null;

$flash = $_SESSION['flash'] ?? ''; unset($_SESSION['flash']);
$csrf = csrf_token();
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>پنل مدیریت · RTM</title>
  <link rel="stylesheet" href="/fonts.css" />
  <link rel="stylesheet" href="/style.css" />
</head>
<body>
  <header>
    <div class="brand">
      <span class="logo">ر</span>
      <div>
        <div class="title">پنل مدیریت RTM</div>
        <div class="subtitle">مدیریت کاربران و آمار · فقط تحلیل، بدون اجرای سفارش</div>
      </div>
    </div>
    <div class="controls">
      <div class="user-chip"><span class="uname"><?= h($me['u']) ?></span><span class="role-badge admin">ادمین</span></div>
      <a class="chip-link" href="index.php">برنامه</a>
      <a class="chip-link logout" href="logout.php">خروج</a>
    </div>
  </header>

  <main class="admin-main">
    <?php if ($flash): ?><div class="admin-flash"><?= h($flash) ?></div><?php endif; ?>

    <section class="admin-col">
      <div class="panel admin-card">
        <h3 class="admin-h">ساخت کاربر جدید</h3>
        <form method="post" class="admin-form" autocomplete="off">
          <input type="hidden" name="action" value="create_user" />
          <input type="hidden" name="csrf" value="<?= h($csrf) ?>" />
          <div class="admin-grid">
            <div><label class="auth-label">نام کاربری</label><input class="auth-input" name="username" required pattern="[A-Za-z0-9_]{3,64}" /></div>
            <div><label class="auth-label">رمز عبور</label><input class="auth-input" name="password" type="password" required minlength="8" /></div>
            <div><label class="auth-label">ایمیل (اختیاری)</label><input class="auth-input" name="email" type="email" /></div>
            <div><label class="auth-label">نقش</label>
              <select name="role" class="auth-input"><option value="user">کاربر</option><option value="admin">ادمین</option></select>
            </div>
          </div>
          <button class="primary admin-submit" type="submit">ساخت کاربر</button>
        </form>
      </div>

      <div class="panel admin-card">
        <h3 class="admin-h">کاربران (<?= count($users) ?>)</h3>
        <div class="user-list">
          <?php foreach ($users as $u => $row):
            $g = $pu[$u] ?? null;
            $closed = (int)($g['closed'] ?? 0); $wins = (int)($g['wins'] ?? 0); $losses=(int)($g['losses'] ?? 0);
            $wr = ($wins+$losses) ? round(100*$wins/($wins+$losses)) : null; ?>
          <div class="user-row">
            <div class="user-main">
              <span class="u-name"><?= h($u) ?></span>
              <span class="role-badge <?= $row['role']==='admin'?'admin':'user' ?>"><?= $row['role']==='admin'?'ادمین':'کاربر' ?></span>
              <span class="pill <?= (int)$row['active']?'on':'off' ?>"><?= (int)$row['active']?'فعال':'غیرفعال' ?></span>
            </div>
            <div class="user-meta num">
              <span title="معاملاتِ ثبت‌شده"><?= (int)($g['total'] ?? 0) ?> معامله</span>
              <?php if ($wr!==null): ?><span title="نرخ برد"> · برد <?= $wr ?>٪</span><?php endif; ?>
              <span class="u-dim"> · آخرین ورود: <?= $row['last_login'] ? h(substr($row['last_login'],0,16)) : '—' ?></span>
            </div>
            <div class="user-acts">
              <form method="post" onsubmit="return true">
                <input type="hidden" name="csrf" value="<?= h($csrf) ?>" />
                <input type="hidden" name="action" value="set_active" />
                <input type="hidden" name="username" value="<?= h($u) ?>" />
                <input type="hidden" name="active" value="<?= (int)$row['active']?0:1 ?>" />
                <button type="submit" class="mini <?= (int)$row['active']?'warn':'good' ?>"><?= (int)$row['active']?'غیرفعال':'فعال' ?></button>
              </form>
              <a class="mini" href="journal.php?user=<?= h($u) ?>" target="_blank">ژورنال</a>
              <form method="post" onsubmit="return confirm('حذفِ کاربر «<?= h($u) ?>» و کلِ ژورنالش؟')">
                <input type="hidden" name="csrf" value="<?= h($csrf) ?>" />
                <input type="hidden" name="action" value="delete_user" />
                <input type="hidden" name="username" value="<?= h($u) ?>" />
                <button type="submit" class="mini danger">حذف</button>
              </form>
            </div>
          </div>
          <?php endforeach; ?>
        </div>
      </div>
    </section>

    <aside class="admin-side">
      <div class="panel admin-card">
        <h3 class="admin-h">آمارِ سیستم (موتورِ یادگیری)</h3>
        <div class="stat-grid">
          <div class="stat"><div class="stat-num num"><?= $sysSetupWR!==null?$sysSetupWR.'٪':'—' ?></div><div class="stat-lab">نرخ بردِ ستاپ‌های پیشنهادی</div></div>
          <div class="stat"><div class="stat-num num"><?= $sysStop!==null?$sysStop.'٪':'—' ?></div><div class="stat-lab">نرخ استاپ</div></div>
          <div class="stat"><div class="stat-num num"><?= $sysExp!==null?($sysExp>=0?'+':'').$sysExp:'—' ?></div><div class="stat-lab">انتظارِ R (خروجِ پله‌ای)</div></div>
          <div class="stat"><div class="stat-num num"><?= $eng['dir_rate']!==null?$eng['dir_rate'].'٪':'—' ?></div><div class="stat-lab">دقتِ جهتِ خام</div></div>
        </div>
        <div class="stat-foot num">فقط ستاپ‌هایی که سیستم پیشنهاد می‌دهد (HTF + فضای کافی + خروجِ پله‌ای) · نمونه: <?= $les['n'] ?: 0 ?> از <?= $les['k'] ?> فایلِ سیگنال</div>
      </div>

      <div class="panel admin-card">
        <h3 class="admin-h">ژورنالِ کاربران</h3>
        <div class="stat-grid">
          <div class="stat"><div class="stat-num num"><?= count($users) ?></div><div class="stat-lab">کاربر</div></div>
          <div class="stat"><div class="stat-num num"><?= (int)$sysTot['total'] ?></div><div class="stat-lab">کلِ معاملات</div></div>
          <div class="stat"><div class="stat-num num"><?= (int)$sysTot['closed'] ?></div><div class="stat-lab">بسته‌شده</div></div>
          <div class="stat"><div class="stat-num num"><?= $sysWR!==null?$sysWR.'٪':'—' ?></div><div class="stat-lab">نرخ بردِ کاربران</div></div>
        </div>
        <div class="stat-foot num">برد <?= (int)$sysTot['wins'] ?> · باخت <?= (int)$sysTot['losses'] ?></div>
      </div>
    </aside>
  </main>
</body>
</html>
