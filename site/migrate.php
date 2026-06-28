<?php
/**
 * Idempotent schema upgrade. require()-able from installer.php; safe to re-run.
 * MySQL < 8 has no "ADD COLUMN IF NOT EXISTS", so every ALTER is guarded via information_schema.
 * Multi-user accounts: rtm_users gains id/email/role/active/last_login; rtm_journal gains user_id.
 */
function rtm_col_exists(mysqli $m, $tbl, $col) {
  $db = $m->query("SELECT DATABASE()")->fetch_row()[0];
  $st = $m->prepare("SELECT 1 FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=? AND COLUMN_NAME=?");
  $st->bind_param('sss', $db, $tbl, $col); $st->execute();
  return (bool)$st->get_result()->fetch_row();
}
function rtm_add_col(mysqli $m, $tbl, $col, $ddl) {
  if (!rtm_col_exists($m, $tbl, $col)) @$m->query("ALTER TABLE `$tbl` ADD COLUMN $ddl");
}

function rtm_migrate(mysqli $m) {
  // base tables (safe to re-run)
  $m->query("CREATE TABLE IF NOT EXISTS rtm_users (username VARCHAR(64) PRIMARY KEY, pass_hash VARCHAR(255), created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
  $m->query("CREATE TABLE IF NOT EXISTS rtm_config (k VARCHAR(64) PRIMARY KEY, v TEXT) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
  $m->query("CREATE TABLE IF NOT EXISTS rtm_journal (
    id INT AUTO_INCREMENT PRIMARY KEY, ts DATETIME DEFAULT CURRENT_TIMESTAMP,
    symbol VARCHAR(24), tf VARCHAR(8), dir VARCHAR(8), src VARCHAR(24), confidence VARCHAR(8),
    entry DOUBLE, sl DOUBLE, tp2 DOUBLE, status VARCHAR(12) DEFAULT 'OPEN', outcome VARCHAR(8) NULL
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

  // extend rtm_users for multi-user + roles
  rtm_add_col($m, 'rtm_users', 'id',         "id INT UNSIGNED NOT NULL AUTO_INCREMENT UNIQUE FIRST");
  rtm_add_col($m, 'rtm_users', 'email',      "email VARCHAR(190) NULL");
  rtm_add_col($m, 'rtm_users', 'role',       "role ENUM('admin','user') NOT NULL DEFAULT 'user'");
  rtm_add_col($m, 'rtm_users', 'active',     "active TINYINT(1) NOT NULL DEFAULT 1");
  rtm_add_col($m, 'rtm_users', 'last_login', "last_login DATETIME NULL");

  // scope journal rows by the owning username
  rtm_add_col($m, 'rtm_journal', 'user_id', "user_id VARCHAR(64) NULL");
  @$m->query("CREATE INDEX idx_journal_user ON rtm_journal(user_id)");

  // failed-login throttle
  $m->query("CREATE TABLE IF NOT EXISTS rtm_login_attempts (
    ip VARCHAR(45) NOT NULL, username VARCHAR(64) NOT NULL, ts DATETIME DEFAULT CURRENT_TIMESTAMP,
    ok TINYINT(1) DEFAULT 0, INDEX idx_ip_ts (ip, ts), INDEX idx_user_ts (username, ts)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");

  // per-user chart drawings (trendlines, rectangles, fib, etc.) — scoped by user+symbol+tf
  $m->query("CREATE TABLE IF NOT EXISTS rtm_drawings (
    id INT AUTO_INCREMENT PRIMARY KEY, user_id VARCHAR(64) NOT NULL,
    symbol VARCHAR(24) NOT NULL, tf VARCHAR(8) NOT NULL,
    type VARCHAR(16) NOT NULL, data TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    INDEX idx_draw (user_id, symbol, tf)
  ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4");
}
