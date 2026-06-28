<?php
/**
 * Session-based auth for the RTM panel. Replaces the old single-user .htpasswd Basic Auth.
 * Provides: require_login / require_admin (page + API aware), CSRF token issue/verify,
 * login/logout, and a failed-attempt rate limiter. Never served directly (denied in .htaccess).
 */
require_once __DIR__ . '/db.php';

if (session_status() !== PHP_SESSION_ACTIVE) {
  session_name('rtm_sess');
  $secure = (($_SERVER['HTTPS'] ?? '') === 'on') || ((int)($_SERVER['SERVER_PORT'] ?? 0) === 443)
            || (($_SERVER['HTTP_X_FORWARDED_PROTO'] ?? '') === 'https');
  session_set_cookie_params([
    'lifetime' => 0, 'path' => '/', 'secure' => $secure,
    'httponly' => true, 'samesite' => 'Strict',
  ]);
  session_start();
}

function is_api_request() {
  return (strpos($_SERVER['HTTP_ACCEPT'] ?? '', 'application/json') !== false)
      || (($_SERVER['REQUEST_METHOD'] ?? 'GET') === 'POST')
      || isset($_GET['api']);
}
function current_user() {
  if (empty($_SESSION['u'])) return null;
  return ['u' => $_SESSION['u'], 'role' => $_SESSION['role'] ?? 'user'];
}
function is_admin() {
  $u = current_user();
  return $u && $u['role'] === 'admin';
}
function require_login() {
  if (!rtm_configured()) {            // fresh deploy not installed yet -> guide to installer
    if (is_api_request()) { http_response_code(503); echo json_encode(['error' => 'not_configured']); }
    else { header('Location: installer.php'); }
    exit;
  }
  if (!current_user()) {
    if (is_api_request()) { http_response_code(401); echo json_encode(['error' => 'auth']); }
    else { header('Location: login.php'); }
    exit;
  }
}
function require_admin() {
  require_login();
  if (!is_admin()) {
    if (is_api_request()) { http_response_code(403); echo json_encode(['error' => 'forbidden']); }
    else { http_response_code(403); echo 'forbidden'; }
    exit;
  }
}

/* ---- CSRF ---- */
function csrf_token() {
  if (empty($_SESSION['csrf'])) $_SESSION['csrf'] = bin2hex(random_bytes(32));
  return $_SESSION['csrf'];
}
function csrf_check($t) {
  return !empty($_SESSION['csrf']) && is_string($t) && hash_equals($_SESSION['csrf'], $t);
}
function csrf_from_request() {
  return $_SERVER['HTTP_X_CSRF'] ?? ($_POST['csrf'] ?? '');
}

/* ---- login / logout ---- */
function login_user(array $row) {
  session_regenerate_id(true);                 // prevent fixation
  $_SESSION['u'] = $row['username'];
  $_SESSION['role'] = $row['role'] ?? 'user';
  $_SESSION['csrf'] = bin2hex(random_bytes(32));
}
function logout_user() {
  $_SESSION = [];
  if (ini_get('session.use_cookies')) {
    $p = session_get_cookie_params();
    setcookie(session_name(), '', time() - 42000, $p['path'], $p['domain'], $p['secure'], $p['httponly']);
  }
  session_destroy();
}

/* ---- rate limit (per IP and per username) ---- */
function client_ip() { return $_SERVER['REMOTE_ADDR'] ?? '0.0.0.0'; }
function login_blocked(mysqli $m, $ip, $user, $max = 5, $win_min = 15) {
  $st = $m->prepare("SELECT COUNT(*) FROM rtm_login_attempts WHERE ok=0 AND ts > (NOW() - INTERVAL ? MINUTE) AND (ip=? OR username=?)");
  $st->bind_param('iss', $win_min, $ip, $user); $st->execute();
  return ((int)$st->get_result()->fetch_row()[0]) >= $max;
}
function login_record(mysqli $m, $ip, $user, $ok) {
  $st = $m->prepare("INSERT INTO rtm_login_attempts (ip, username, ok) VALUES (?,?,?)");
  $okv = $ok ? 1 : 0; $st->bind_param('ssi', $ip, $user, $okv); $st->execute();
}
