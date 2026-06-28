<?php
require_once __DIR__ . '/auth.php';

// not installed yet -> send to installer
if (!rtm_configured()) { header('Location: installer.php'); exit; }
// already signed in -> go to the app (or admin)
if (current_user()) { header('Location: ' . (is_admin() ? 'admin.php' : 'index.php')); exit; }

$err = '';
if (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST') {
  $m = rtm_db();
  $user = trim($_POST['username'] ?? '');
  $pass = (string)($_POST['password'] ?? '');
  $ip = client_ip();
  if (!$m) {
    $err = 'اتصال به دیتابیس برقرار نشد.';
  } elseif (!csrf_check($_POST['csrf'] ?? '')) {
    $err = 'نشستِ شما منقضی شده؛ دوباره تلاش کن.';
  } elseif (login_blocked($m, $ip, $user)) {
    $err = 'تلاشِ ناموفقِ زیاد. چند دقیقه بعد دوباره امتحان کن.';
  } else {
    $st = $m->prepare("SELECT username, pass_hash, role, active FROM rtm_users WHERE username=?");
    $st->bind_param('s', $user); $st->execute();
    $row = $st->get_result()->fetch_assoc();
    if ($row && (int)$row['active'] === 1 && password_verify($pass, $row['pass_hash'])) {
      login_record($m, $ip, $user, 1);
      $up = $m->prepare("UPDATE rtm_users SET last_login=NOW() WHERE username=?"); $up->bind_param('s', $user); $up->execute();
      login_user($row);
      header('Location: ' . (($row['role'] ?? 'user') === 'admin' ? 'admin.php' : 'index.php'));
      exit;
    }
    login_record($m, $ip, $user, 0);
    $err = 'نام کاربری یا رمز عبور اشتباه است.';   // generic: never reveal which / disabled
  }
}
$csrf = csrf_token();
?>
<!doctype html>
<html lang="fa" dir="rtl">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>ورود · دستیار معاملاتی RTM</title>
  <link rel="stylesheet" href="/fonts.css" />
  <link rel="stylesheet" href="/style.css" />
</head>
<body class="auth-body">
  <main class="auth-wrap">
    <form class="auth-card" method="post" autocomplete="off">
      <div class="auth-brand">
        <span class="logo">ر</span>
        <div>
          <div class="title">دستیار معاملاتی RTM</div>
          <div class="subtitle">ورود به پنل · فقط تحلیل، بدون اجرای سفارش</div>
        </div>
      </div>

      <?php if ($err): ?><div class="auth-err"><?= htmlspecialchars($err, ENT_QUOTES) ?></div><?php endif; ?>

      <label class="auth-label" for="username">نام کاربری</label>
      <input class="auth-input" id="username" name="username" required autofocus value="<?= htmlspecialchars($_POST['username'] ?? '', ENT_QUOTES) ?>" />

      <label class="auth-label" for="password">رمز عبور</label>
      <input class="auth-input" id="password" name="password" type="password" required />

      <input type="hidden" name="csrf" value="<?= htmlspecialchars($csrf, ENT_QUOTES) ?>" />
      <button class="primary auth-submit" type="submit">ورود</button>
      <div class="auth-foot">دسترسی فقط برای کاربرانِ مجاز است.</div>
    </form>
  </main>
</body>
</html>
