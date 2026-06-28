<?php
/**
 * RTM STATIC-mode installer for a cPanel host WITHOUT Python.
 * The Python engine runs on GitHub Actions and commits signal JSON into this repo; this host
 * only SERVES the static site, protects it with a login, and `git pull`s the fresh data on a
 * cron. No Python, no venv, no token needed (public repo pulls without credentials).
 *
 * Steps it does: connect DB + create tables + store your site login; write config.local.php
 * (600); write .htpasswd + .htaccess (Basic Auth over the whole site + deny secrets); install
 * a `git pull` cron (or show the line). Then DELETE this installer.
 */
error_reporting(E_ALL & ~E_DEPRECATED & ~E_NOTICE);
@set_time_limit(300);
$ROOT = __DIR__; $LOCK = "$ROOT/.installed"; $CFG = "$ROOT/config.local.php";
function sh_ok(){ return function_exists('shell_exec') && !in_array('shell_exec', array_map('trim', explode(',', ini_get('disable_functions')?:''))); }
function sh($c){ return sh_ok()? trim((string)@shell_exec($c.' 2>&1')) : ''; }
function h($s){ return htmlspecialchars((string)$s, ENT_QUOTES, 'UTF-8'); }

$done=[]; $errs=[]; $cron=''; $installed=file_exists($LOCK);

if ($_SERVER['REQUEST_METHOD']==='POST' && !$installed) {
  $dbh=trim($_POST['db_host']??'localhost'); $dbn=trim($_POST['db_name']??'');
  $dbu=trim($_POST['db_user']??''); $dbp=(string)($_POST['db_pass']??'');
  $su=trim($_POST['site_user']??''); $sp=(string)($_POST['site_pass']??'');
  $repo=trim($_POST['gh_repo']??''); $mins=max(10,(int)($_POST['mins']??15));

  // 1) DB + schema migration + first ADMIN account
  try {
    $m=@new mysqli($dbh,$dbu,$dbp,$dbn);
    if($m->connect_errno) throw new Exception($m->connect_error);
    require __DIR__.'/migrate.php'; rtm_migrate($m);                 // idempotent multi-user schema
    if($su&&$sp){
      $ph=password_hash($sp,PASSWORD_BCRYPT);
      $st=$m->prepare("INSERT INTO rtm_users (username,pass_hash,role,active) VALUES (?,?, 'admin',1)
                       ON DUPLICATE KEY UPDATE pass_hash=VALUES(pass_hash), role='admin', active=1");
      $st->bind_param('ss',$su,$ph); $st->execute();
      $m->query("UPDATE rtm_journal SET user_id=".($su?("'".$m->real_escape_string($su)."'"):"user_id")." WHERE user_id IS NULL");  // adopt legacy rows
    }
    $st=$m->prepare("REPLACE INTO rtm_config (k,v) VALUES ('gh_repo',?)"); $st->bind_param('s',$repo); $st->execute();
    $m->close(); $done[]="دیتابیس متصل، جدول‌ها مهاجرت داده شد؛ حسابِ ادمین ساخته شد.";
  } catch(Exception $e){ $errs[]="اتصال دیتابیس ناموفق: ".$e->getMessage(); }

  // 2) config.local.php (used by journal.php)
  if(!$errs){
    $c="<?php\n// AUTO-GENERATED. chmod 600. do not commit.\nreturn ".var_export(['db'=>compact('dbh','dbn','dbu','dbp'),'site_user'=>$su,'gh_repo'=>$repo],true).";\n";
    if(@file_put_contents($CFG,$c)!==false){ @chmod($CFG,0600); $done[]="config.local.php نوشته شد (۶۰۰)."; }
    else $errs[]="نوشتنِ config.local.php ناموفق.";
  }

  // 3) login = PHP sessions (login.php). .htaccess only denies secrets/includes; the app pages
  //    (index.php/journal.php/admin.php) gate themselves via auth.php. Basic Auth is dropped so
  //    multiple accounts, roles, disable/delete and a logout button can work.
  if($su&&$sp){
    @unlink("$ROOT/.htpasswd");
    $ht="DirectoryIndex index.php index.html\n".
        "<FilesMatch \"^(config\\.local\\.php|\\.htpasswd|\\.installed|db\\.php|auth\\.php|migrate\\.php)$\">\n  Require all denied\n</FilesMatch>\n".
        "<Files \"installer.php\">\n  Require all denied\n</Files>\n".
        "RedirectMatch 404 /\\.git\n";
    @file_put_contents("$ROOT/.htaccess",$ht);
    $done[]="ورودِ مبتنی بر نشست فعال شد (login.php) و دسترسیِ مستقیم به فایل‌های حساس بسته شد.";
  } else $errs[]="یوزر/پسوردِ ادمین خالی است.";

  // 4) git pull cron (public repo -> no token)
  $cron="*/$mins * * * * cd ".escapeshellarg($ROOT)." && git pull --rebase --autostash >> ".escapeshellarg("$ROOT/data/pull.log")." 2>&1";
  if(sh_ok()){
    if(!is_dir("$ROOT/.git") && $repo) sh("cd ".escapeshellarg($ROOT)." && git init -q && git remote add origin ".escapeshellarg($repo)." && git fetch -q origin && git checkout -q -t origin/main 2>/dev/null || true");
    $cur=sh("crontab -l"); $cur=preg_replace('/# >>> RTM >>>.*# <<< RTM <<<\n?/s','',$cur);
    $tmp=tempnam(sys_get_temp_dir(),'cr'); file_put_contents($tmp, trim($cur)."\n# >>> RTM >>>\n$cron\n# <<< RTM <<<\n");
    sh("crontab ".escapeshellarg($tmp)); @unlink($tmp);
    if((int)sh("crontab -l | grep -c RTM")>0) $done[]="کرانِ git pull نصب شد."; else $errs[]="نصبِ خودکارِ کران ناموفق — خطِ زیر را دستی بگذار.";
  } else $errs[]="shell بسته است — خطِ کرانِ زیر را دستی در cPanel → Cron Jobs بگذار.";

  if(!$errs){ @file_put_contents($LOCK,date('c')); $done[]="نصب کامل شد و قفل شد."; }
}
?>
<!doctype html><html lang="fa" dir="rtl"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>نصبِ RTM (حالتِ بدونِ پایتون)</title>
<style>body{font-family:Tahoma;background:#0b0d10;color:#e7ecf2;max-width:640px;margin:24px auto;padding:0 16px}
h1{color:#d4af37;font-size:20px}label{display:block;margin:12px 0 4px;font-size:13px;color:#a7b0bd}
input{width:100%;padding:10px;border:1px solid #2a2f37;border-radius:8px;background:#12151a;color:#e7ecf2;box-sizing:border-box}
button{margin-top:16px;width:100%;padding:12px;background:#d4af37;color:#1a1407;border:0;border-radius:10px;font-weight:700;cursor:pointer}
.ok{color:#2bb98a}.err{color:#ef5b6b}.box{background:#12151a;border:1px solid #2a2f37;border-radius:10px;padding:12px;margin:10px 0}
pre{background:#0e1116;border:1px solid #2a2f37;border-radius:8px;padding:10px;overflow:auto;font-size:12px}.grid{display:grid;grid-template-columns:1fr 1fr;gap:10px}small{color:#8a93a0}</style></head><body>
<h1>نصبِ دستیار RTM — حالتِ هاستِ بدونِ پایتون</h1>
<div class="box"><small>محاسبه روی GitHub Actions انجام می‌شود؛ این هاست فقط سایت را نشان می‌دهد و هر چند دقیقه دیتا را <code>git pull</code> می‌کند.</small></div>
<?php if($installed && $_SERVER['REQUEST_METHOD']!=='POST'): ?><div class="box err">قبلاً نصب شده. برای نصبِ دوباره <code>.installed</code> را حذف کن.</div><?php endif; ?>
<?php if($done||$errs): ?><div class="box"><?php foreach($done as $d)echo "<div class='ok'>✓ ".h($d)."</div>"; foreach($errs as $e)echo "<div class='err'>✗ ".h($e)."</div>"; ?></div>
<?php if($cron): ?><div class="box"><b>خطِ کران</b> (اگر خودکار نشد، در cPanel → Cron Jobs بگذار):<pre><?=h($cron)?></pre></div><?php endif; ?>
<?php if(!$errs): ?><div class="box ok">آماده است. به <code>login.php</code> برو و با حسابِ ادمین وارد شو؛ از <code>پنل مدیریت</code> کاربر بساز. <b>installer.php را حذف کن.</b></div><?php endif; ?>
<?php endif; ?>
<?php if(!$installed||$errs): ?>
<form method="post">
  <div class="box"><small>shell: <?=sh_ok()?'<span class=ok>هست</span>':'<span class=err>بسته</span>'?> · git: <?=sh('command -v git')?'<span class=ok>هست</span>':'<span class=err>؟</span>'?></small></div>
  <b>دیتابیس MySQL</b>
  <div class="grid"><div><label>هاست</label><input name="db_host" value="localhost"></div><div><label>نام دیتابیس</label><input name="db_name" required></div>
  <div><label>یوزر دیتابیس</label><input name="db_user" required></div><div><label>پسورد دیتابیس</label><input name="db_pass" type="password"></div></div>
  <b>حسابِ ادمینِ اول</b>
  <div class="grid"><div><label>یوزرنیم</label><input name="site_user" required></div><div><label>پسورد (حداقل ۸ کاراکتر)</label><input name="site_pass" type="password" required minlength="8"></div></div>
  <b>گیت‌هاب</b>
  <label>آدرسِ ریپوی عمومی</label><input name="gh_repo" placeholder="https://github.com/you/rtm.git" required>
  <label>هر چند دقیقه pull شود</label><input name="mins" type="number" value="15" min="10">
  <button type="submit">نصب کن</button>
</form><?php endif; ?>
</body></html>
