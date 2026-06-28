<?php
/**
 * Shared MySQL connector. Reads creds from config.local.php (written by installer, chmod 600).
 * Single source of truth used by auth.php / journal.php / admin.php. Never served directly
 * (the installer's .htaccess denies it); it only produces output when require()'d by a page.
 */
function rtm_cfg() {
  static $c = null;
  if ($c !== null) return $c;
  $f = __DIR__ . '/config.local.php';
  $c = file_exists($f) ? (require $f) : [];
  return $c;
}
function rtm_configured() {
  $c = rtm_cfg();
  return !empty($c['db']) && !empty($c['db']['dbn']);
}
function rtm_db() {
  static $m = null;
  if ($m) return $m;
  $c = rtm_cfg(); $d = $c['db'] ?? [];
  $m = @new mysqli($d['dbh'] ?? 'localhost', $d['dbu'] ?? '', $d['dbp'] ?? '', $d['dbn'] ?? '');
  if ($m->connect_errno) return null;
  $m->set_charset('utf8mb4');
  return $m;
}
