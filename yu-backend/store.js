import crypto from "node:crypto";
import fs from "node:fs";
import path from "node:path";

export const ROOT = path.resolve(process.env.AIASSISTANCE_SERVER_ROOT || process.cwd());
export const DATA_DIR = path.resolve(process.env.AIASSISTANCE_DATA_DIR || path.join(ROOT, "data"));
export const PACKAGE_DIR = path.resolve(process.env.AIASSISTANCE_PACKAGE_DIR || path.join(ROOT, "packages"));
export const CORE_RELEASE_DIR = path.resolve(process.env.AIASSISTANCE_CORE_RELEASE_DIR || path.join(ROOT, "core-releases"));
export const CORE_PACKAGE_DIR = path.resolve(process.env.AIASSISTANCE_CORE_PACKAGE_DIR || path.join(DATA_DIR, "core-packages"));
export const USB_PROXY_RELEASE_DIR = path.resolve(process.env.AIASSISTANCE_USB_PROXY_RELEASE_DIR || path.join(ROOT, "usb-proxy-releases"));
export const USB_PROXY_PACKAGE_DIR = path.resolve(process.env.AIASSISTANCE_USB_PROXY_PACKAGE_DIR || path.join(DATA_DIR, "usb-proxy-packages"));
export const HAILO_RELEASE_DIR = path.resolve(process.env.AIASSISTANCE_HAILO_RELEASE_DIR || path.join(ROOT, "hailo-releases"));
export const HAILO_PACKAGE_DIR = path.resolve(process.env.AIASSISTANCE_HAILO_PACKAGE_DIR || path.join(DATA_DIR, "hailo-packages"));
export const PRIVATE_KEY_PATH = path.resolve(process.env.AIASSISTANCE_PRIVATE_KEY || path.join(DATA_DIR, "license_private.pem"));
export const CORE_SECRET_PATH = path.resolve(process.env.AIASSISTANCE_CORE_SECRET || path.join(DATA_DIR, "core_secret.bin"));
export const USB_PROXY_SECRET_PATH = path.resolve(process.env.AIASSISTANCE_USB_PROXY_SECRET || path.join(DATA_DIR, "usb_proxy_secret.bin"));
export const CORE_VERSION = process.env.AIASSISTANCE_CORE_VERSION || "local";
export const USB_PROXY_VERSION = process.env.AIASSISTANCE_USB_PROXY_VERSION || "local";
export const HAILO_VERSION = process.env.AIASSISTANCE_HAILO_VERSION || "4.23.0";
export const HAILO_KERNEL_RELEASE = process.env.AIASSISTANCE_HAILO_KERNEL_RELEASE || "5.10.160-rockchip-rk3588";

const KEY_CHARS = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789";
const DEFAULT_FEATURES = ["capture", "inference", "aim", "ota"];
export const UI_BRANDS = ["yu", "xh", "xcsh"];
const UI_BRAND_SET = new Set(UI_BRANDS);
const TRIAL_CHECK_INTERVAL_SECONDS = 5 * 60;
const TRIAL_GRACE_SECONDS = 5 * 60;
const TRIAL_GRANT_VALID_SECONDS = 10 * 60;
const ISSUED_PACKAGE_TTL_MS = positiveIntegerEnv("AIASSISTANCE_ISSUED_PACKAGE_TTL_MS", 30 * 60 * 1000);
const ISSUED_PACKAGE_ORPHAN_GRACE_MS = positiveIntegerEnv("AIASSISTANCE_ISSUED_PACKAGE_ORPHAN_GRACE_MS", 2 * 60 * 60 * 1000);
const MODEL_KEY_BYTES = 32;
const DEFAULT_XCSH_PRICING = {
  permanent_price_cents: 0,
  trial_1d_price_cents: 0,
  updated_at: ""
};

export function ensureServerDirs() {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  fs.mkdirSync(PACKAGE_DIR, { recursive: true });
  fs.mkdirSync(CORE_PACKAGE_DIR, { recursive: true });
  fs.mkdirSync(USB_PROXY_PACKAGE_DIR, { recursive: true });
  fs.mkdirSync(HAILO_PACKAGE_DIR, { recursive: true });
}

function positiveIntegerEnv(name, fallback) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
}

export function jsonPath(name) {
  return path.join(DATA_DIR, name);
}

export function loadJson(name, fallback) {
  const file = jsonPath(name);
  if (!fs.existsSync(file)) return fallback;
  return JSON.parse(fs.readFileSync(file, "utf8"));
}

export function saveJson(name, value) {
  fs.mkdirSync(DATA_DIR, { recursive: true });
  const finalPath = jsonPath(name);
  const tempPath = `${finalPath}.tmp-${process.pid}-${Date.now()}`;
  fs.writeFileSync(tempPath, `${JSON.stringify(value, null, 2)}\n`, { mode: 0o600 });
  fs.renameSync(tempPath, finalPath);
  try {
    fs.chmodSync(finalPath, 0o600);
  } catch {
    // chmod can fail on some mounted filesystems; the atomic write above is still valid.
  }
}

const ISSUED_PACKAGE_STORES = {
  core: { tokenFile: "core-tokens.json", packageDir: CORE_PACKAGE_DIR },
  usb_proxy: { tokenFile: "usb-proxy-tokens.json", packageDir: USB_PROXY_PACKAGE_DIR },
  hailo: { tokenFile: "hailo-tokens.json", packageDir: HAILO_PACKAGE_DIR }
};

function issuedPackageStore(kind) {
  const store = ISSUED_PACKAGE_STORES[kind];
  if (!store) {
    throw new Error(`unknown issued package kind: ${kind}`);
  }
  return store;
}

function safeIssuedFileName(value) {
  const fileName = path.basename(String(value || ""));
  return fileName && fileName !== "." && fileName !== ".." ? fileName : "";
}

function issuedPackagePath(kind, record) {
  if (kind === "hailo" && record?.source_path) {
    const sourcePath = path.resolve(String(record.source_path));
    const releaseRoot = path.resolve(HAILO_RELEASE_DIR);
    if (sourcePath === releaseRoot || sourcePath.startsWith(`${releaseRoot}${path.sep}`)) {
      return sourcePath;
    }
    return "";
  }
  const store = issuedPackageStore(kind);
  const fileName = safeIssuedFileName(record?.file);
  return fileName ? path.join(store.packageDir, fileName) : "";
}

function removeFileIfPresent(filePath) {
  if (!filePath) return false;
  try {
    if (fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
      fs.unlinkSync(filePath);
      return true;
    }
  } catch {
    return false;
  }
  return false;
}

function cleanupIssuedPackageStore(kind, now) {
  const store = issuedPackageStore(kind);
  fs.mkdirSync(store.packageDir, { recursive: true });
  const tokens = loadJson(store.tokenFile, {});
  const nextTokens = tokens && typeof tokens === "object" ? { ...tokens } : {};
  const referencedFiles = new Set();
  const deletedFiles = [];
  let changed = false;

  for (const [token, record] of Object.entries(nextTokens)) {
    const expiresAt = Number(record?.expires_at_ms || 0);
    const filePath = issuedPackagePath(kind, record);
    const fileName = safeIssuedFileName(record?.file);
    const fileExists = filePath && fs.existsSync(filePath);
    if (!record || !filePath || !fileExists || !expiresAt || now > expiresAt) {
      if (kind !== "hailo" || !record?.source_path) {
        if (removeFileIfPresent(filePath)) deletedFiles.push(filePath);
      }
      delete nextTokens[token];
      changed = true;
      continue;
    }
    if (fileName && !(kind === "hailo" && record?.source_path)) {
      referencedFiles.add(fileName);
    }
  }

  const orphanCutoff = now - ISSUED_PACKAGE_ORPHAN_GRACE_MS;
  for (const entry of fs.readdirSync(store.packageDir, { withFileTypes: true })) {
    if (!entry.isFile() || referencedFiles.has(entry.name)) continue;
    if (!entry.name.endsWith(".enc") && !entry.name.endsWith(".tar.gz") && !entry.name.endsWith(".tar.zst")) continue;
    const filePath = path.join(store.packageDir, entry.name);
    try {
      if (fs.statSync(filePath).mtimeMs <= orphanCutoff && removeFileIfPresent(filePath)) {
        deletedFiles.push(filePath);
      }
    } catch {
      // Ignore races with concurrent cleanup or manual file removal.
    }
  }

  if (changed) {
    saveJson(store.tokenFile, nextTokens);
  }
  return {
    tokens_removed: Object.keys(tokens || {}).length - Object.keys(nextTokens).length,
    files_deleted: deletedFiles
  };
}

export function cleanupIssuedPackages(now = Date.now()) {
  return {
    core: cleanupIssuedPackageStore("core", now),
    usb_proxy: cleanupIssuedPackageStore("usb_proxy", now),
    hailo: cleanupIssuedPackageStore("hailo", now)
  };
}

export function resolveIssuedPackage(kind, token, now = Date.now()) {
  cleanupIssuedPackageStore(kind, now);
  const store = issuedPackageStore(kind);
  const tokens = loadJson(store.tokenFile, {});
  const record = tokens && typeof tokens === "object" ? tokens[token] : null;
  if (!record || now > Number(record.expires_at_ms || 0)) {
    return null;
  }
  const filePath = issuedPackagePath(kind, record);
  if (!filePath || !fs.existsSync(filePath) || !fs.statSync(filePath).isFile()) {
    return null;
  }
  return {
    record,
    fileName: safeIssuedFileName(record.file) || path.basename(filePath),
    filePath
  };
}

export function consumeIssuedPackage(kind, token) {
  const store = issuedPackageStore(kind);
  const tokens = loadJson(store.tokenFile, {});
  const nextTokens = tokens && typeof tokens === "object" ? { ...tokens } : {};
  const record = nextTokens[token];
  if (!record) {
    return { consumed: false, file_deleted: false };
  }
  const filePath = issuedPackagePath(kind, record);
  delete nextTokens[token];
  saveJson(store.tokenFile, nextTokens);
  const shouldDeleteFile = !(kind === "hailo" && record.source_path);
  return {
    consumed: true,
    file_deleted: shouldDeleteFile ? removeFileIfPresent(filePath) : false
  };
}

export function sha256(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function sha256Buffer(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function stableJson(value) {
  if (Array.isArray(value)) {
    return `[${value.map((item) => stableJson(item)).join(",")}]`;
  }
  if (value && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function canonicalLicensePayload(payload) {
  const clone = { ...payload };
  delete clone.signature;
  delete clone.signature_algorithm;
  return stableJson(clone);
}

export function signLicense(payload) {
  const privateKey = fs.readFileSync(PRIVATE_KEY_PATH, "utf8");
  return crypto.sign(null, Buffer.from(canonicalLicensePayload(payload)), privateKey).toString("base64");
}

export function requirePrivateKey() {
  if (!fs.existsSync(PRIVATE_KEY_PATH)) {
    throw new Error(`missing private key: ${PRIVATE_KEY_PATH}. Run npm run gen-keypair first.`);
  }
}

function encryptionSecret() {
  return String(process.env.AIASSISTANCE_KEY_EXPORT_SECRET || "").trim();
}

function encryptionKey(secret) {
  return crypto.createHash("sha256").update(secret).digest();
}

export function encryptLicenseKey(plainKey) {
  const secret = encryptionSecret();
  if (!secret) {
    throw new Error("AIASSISTANCE_KEY_EXPORT_SECRET is required to encrypt exportable license keys");
  }
  const nonce = crypto.randomBytes(12);
  const cipher = crypto.createCipheriv("aes-256-gcm", encryptionKey(secret), nonce);
  cipher.setAAD(Buffer.from("aiassistance-license-key"));
  const ciphertext = Buffer.concat([cipher.update(String(plainKey), "utf8"), cipher.final()]);
  const tag = cipher.getAuthTag();
  return {
    format: "aikey1",
    nonce_b64: nonce.toString("base64"),
    tag_b64: tag.toString("base64"),
    ciphertext_b64: ciphertext.toString("base64")
  };
}

export function decryptLicenseKey(encrypted) {
  const secret = encryptionSecret();
  if (!secret) {
    throw new Error("AIASSISTANCE_KEY_EXPORT_SECRET is required to decrypt license keys");
  }
  if (!encrypted || encrypted.format !== "aikey1") {
    throw new Error("license key is not exportable");
  }
  const decipher = crypto.createDecipheriv(
    "aes-256-gcm",
    encryptionKey(secret),
    Buffer.from(encrypted.nonce_b64 || "", "base64")
  );
  decipher.setAAD(Buffer.from("aiassistance-license-key"));
  decipher.setAuthTag(Buffer.from(encrypted.tag_b64 || "", "base64"));
  const plaintext = Buffer.concat([
    decipher.update(Buffer.from(encrypted.ciphertext_b64 || "", "base64")),
    decipher.final()
  ]);
  return plaintext.toString("utf8");
}

function randomSegment(length) {
  let value = "";
  while (value.length < length) {
    const byte = crypto.randomBytes(1)[0];
    if (byte >= Math.floor(256 / KEY_CHARS.length) * KEY_CHARS.length) continue;
    value += KEY_CHARS[byte % KEY_CHARS.length];
  }
  return value;
}

export function generateLicenseKeyText() {
  return `AA-${randomSegment(4)}-${randomSegment(4)}-${randomSegment(4)}`;
}

export function normalizePlan(value) {
  return String(value || "").trim().toLowerCase() === "trial" ? "trial" : "permanent";
}

export function normalizeUiBrand(value) {
  const normalized = String(value || "").trim().toLowerCase();
  return UI_BRAND_SET.has(normalized) ? normalized : "yu";
}

export function compareVersionText(left, right) {
  const leftParts = String(left || "").match(/\d+|[A-Za-z]+|[^A-Za-z\d]+/g) || [];
  const rightParts = String(right || "").match(/\d+|[A-Za-z]+|[^A-Za-z\d]+/g) || [];
  const length = Math.max(leftParts.length, rightParts.length);
  for (let index = 0; index < length; index += 1) {
    const leftPart = leftParts[index] || "";
    const rightPart = rightParts[index] || "";
    if (leftPart === rightPart) continue;
    const leftNumber = /^\d+$/.test(leftPart) ? Number(leftPart) : NaN;
    const rightNumber = /^\d+$/.test(rightPart) ? Number(rightPart) : NaN;
    if (Number.isFinite(leftNumber) && Number.isFinite(rightNumber)) {
      return leftNumber - rightNumber;
    }
    return leftPart.localeCompare(rightPart);
  }
  return 0;
}

export function normalizeTrialDurationSeconds(value, fallback = 7 * 24 * 60 * 60) {
  const seconds = Math.floor(Number(value) || 0);
  return seconds > 0 ? Math.min(seconds, 366 * 24 * 60 * 60) : fallback;
}

export function normalizeCents(value, fallback = 0) {
  const cents = Math.floor(Number(value));
  return Number.isFinite(cents) ? Math.max(0, cents) : fallback;
}

export function normalizeAdminUsername(value) {
  return String(value || "").trim().toLowerCase().replace(/[^a-z0-9_.-]+/g, "");
}

export function hashAdminPassword(password) {
  const text = String(password || "");
  if (text.length < 6) {
    throw new Error("password must be at least 6 characters");
  }
  const salt = crypto.randomBytes(16);
  const hash = crypto.scryptSync(text, salt, 32);
  return {
    format: "scrypt1",
    salt_b64: salt.toString("base64"),
    hash_b64: hash.toString("base64"),
    key_length: hash.length
  };
}

export function verifyAdminPassword(password, passwordHash) {
  if (!passwordHash || passwordHash.format !== "scrypt1") return false;
  const salt = Buffer.from(String(passwordHash.salt_b64 || ""), "base64");
  const expected = Buffer.from(String(passwordHash.hash_b64 || ""), "base64");
  const keyLength = Number(passwordHash.key_length || expected.length || 32);
  if (!salt.length || !expected.length || !Number.isFinite(keyLength) || keyLength <= 0) return false;
  const actual = crypto.scryptSync(String(password || ""), salt, keyLength);
  return actual.length === expected.length && crypto.timingSafeEqual(actual, expected);
}

function normalizeXcshPricing(value) {
  const source = value && typeof value === "object" ? value : {};
  return {
    permanent_price_cents: normalizeCents(source.permanent_price_cents, DEFAULT_XCSH_PRICING.permanent_price_cents),
    trial_1d_price_cents: normalizeCents(source.trial_1d_price_cents, DEFAULT_XCSH_PRICING.trial_1d_price_cents),
    updated_at: String(source.updated_at || "")
  };
}

export function readPricing() {
  const raw = loadJson("pricing.json", {});
  const source = raw && typeof raw === "object" ? raw : {};
  return {
    xcsh: normalizeXcshPricing(source.xcsh)
  };
}

export function writePricing(pricing) {
  const source = pricing && typeof pricing === "object" ? pricing : {};
  const current = readPricing();
  const next = {
    xcsh: normalizeXcshPricing(source.xcsh || current.xcsh)
  };
  saveJson("pricing.json", next);
  return next;
}

export function writeXcshPricing(payload) {
  const pricing = readPricing();
  pricing.xcsh = {
    permanent_price_cents: normalizeCents(payload?.permanent_price_cents),
    trial_1d_price_cents: normalizeCents(payload?.trial_1d_price_cents),
    updated_at: isoNow()
  };
  return writePricing(pricing).xcsh;
}

function normalizeAdminRecord(username, record) {
  const source = record && typeof record === "object" ? record : {};
  return {
    username,
    password_hash: source.password_hash && typeof source.password_hash === "object" ? source.password_hash : {},
    enabled: source.enabled !== false,
    balance_cents: Math.floor(Number(source.balance_cents) || 0),
    created_at: String(source.created_at || ""),
    updated_at: String(source.updated_at || ""),
    password_changed_at: String(source.password_changed_at || ""),
    disabled_at: String(source.disabled_at || ""),
    note: String(source.note || "")
  };
}

export function readAdminUsers() {
  const raw = loadJson("admin_users.json", {});
  const source = raw && typeof raw === "object" ? raw : {};
  const result = {};
  const users = source.users && typeof source.users === "object" ? source.users : source;
  for (const [rawUsername, record] of Object.entries(users || {})) {
    const username = normalizeAdminUsername(rawUsername || record?.username);
    if (!username) continue;
    result[username] = normalizeAdminRecord(username, record);
  }
  return result;
}

export function writeAdminUsers(users) {
  const result = {};
  for (const [rawUsername, record] of Object.entries(users || {})) {
    const username = normalizeAdminUsername(rawUsername || record?.username);
    if (!username) continue;
    result[username] = normalizeAdminRecord(username, record);
  }
  saveJson("admin_users.json", {
    users: result,
    updated_at: isoNow()
  });
  return result;
}

export function upsertAdminUser(usernameValue, password = "", options = {}) {
  const username = normalizeAdminUsername(usernameValue);
  if (!username) {
    throw new Error("username is required");
  }
  const users = readAdminUsers();
  const now = isoNow();
  const existing = users[username] || {};
  const next = normalizeAdminRecord(username, {
    ...existing,
    username,
    enabled: options.enabled ?? existing.enabled ?? true,
    balance_cents: existing.balance_cents || 0,
    note: options.note ?? existing.note ?? "",
    created_at: existing.created_at || now,
    updated_at: now
  });
  if (password) {
    next.password_hash = hashAdminPassword(password);
    next.password_changed_at = now;
  } else if (!next.password_hash?.hash_b64) {
    throw new Error("password is required for a new admin user");
  }
  users[username] = next;
  writeAdminUsers(users);
  return next;
}

export function setAdminUserEnabled(usernameValue, enabled) {
  const username = normalizeAdminUsername(usernameValue);
  const users = readAdminUsers();
  if (!users[username]) {
    throw new Error("admin user not found");
  }
  users[username].enabled = Boolean(enabled);
  users[username].updated_at = isoNow();
  if (enabled) {
    users[username].disabled_at = "";
  } else {
    users[username].disabled_at = users[username].updated_at;
  }
  writeAdminUsers(users);
  return users[username];
}

export function deleteAdminUser(usernameValue) {
  const username = normalizeAdminUsername(usernameValue);
  const users = readAdminUsers();
  const existing = users[username];
  if (!existing) {
    throw new Error("admin user not found");
  }
  delete users[username];
  writeAdminUsers(users);
  return existing;
}

export function readBalanceLedger() {
  const raw = loadJson("balance_ledger.json", []);
  if (Array.isArray(raw)) return raw.filter((item) => item && typeof item === "object");
  if (raw && typeof raw === "object" && Array.isArray(raw.entries)) {
    return raw.entries.filter((item) => item && typeof item === "object");
  }
  return [];
}

export function writeBalanceLedger(entries) {
  const clean = Array.isArray(entries) ? entries.filter((item) => item && typeof item === "object") : [];
  saveJson("balance_ledger.json", clean);
  return clean;
}

export function appendBalanceLedger(entry) {
  const ledger = readBalanceLedger();
  const item = {
    id: `bal_${Date.now()}_${crypto.randomBytes(4).toString("hex")}`,
    created_at: isoNow(),
    ...entry
  };
  ledger.push(item);
  writeBalanceLedger(ledger);
  return item;
}

export function adjustAdminBalance(usernameValue, deltaCents, options = {}) {
  const username = normalizeAdminUsername(usernameValue);
  const delta = Math.floor(Number(deltaCents) || 0);
  const users = readAdminUsers();
  if (!users[username]) {
    throw new Error("admin user not found");
  }
  const before = Math.floor(Number(users[username].balance_cents) || 0);
  const after = before + delta;
  if (after < 0) {
    throw new Error("insufficient balance");
  }
  users[username].balance_cents = after;
  users[username].updated_at = isoNow();
  writeAdminUsers(users);
  appendBalanceLedger({
    username,
    actor: String(options.actor || ""),
    type: String(options.type || (delta >= 0 ? "adjust" : "charge")),
    amount_cents: delta,
    balance_before_cents: before,
    balance_after_cents: after,
    reason: String(options.reason || ""),
    batch_id: String(options.batch_id || "")
  });
  return users[username];
}

function normalizeModelKeyB64(value) {
  const text = String(value || "").trim();
  if (!text) return "";
  try {
    const bytes = Buffer.from(text, "base64");
    return bytes.length === MODEL_KEY_BYTES ? bytes.toString("base64") : "";
  } catch {
    return "";
  }
}

function normalizeModelKeyInput(value) {
  if (value && typeof value === "object") {
    return normalizeModelKeyB64(value.key_b64 || value.model_key_b64);
  }
  return normalizeModelKeyB64(value);
}

function newModelKeyB64() {
  return crypto.randomBytes(MODEL_KEY_BYTES).toString("base64");
}

export function isoNow() {
  return new Date().toISOString();
}

export function addSecondsIso(isoValue, seconds) {
  const baseMs = isoValue ? Date.parse(isoValue) : Date.now();
  const startMs = Number.isFinite(baseMs) ? baseMs : Date.now();
  return new Date(startMs + Math.max(0, Number(seconds) || 0) * 1000).toISOString();
}

export function isExpiredIso(isoValue, nowMs = Date.now()) {
  if (!isoValue) return false;
  const expiresMs = Date.parse(isoValue);
  return Number.isFinite(expiresMs) && expiresMs <= nowMs;
}

export function newBatchId() {
  const stamp = new Date().toISOString().replace(/[-:TZ.]/g, "").slice(0, 14);
  return `batch_${stamp}_${crypto.randomBytes(3).toString("hex")}`;
}

export function addLicenseKey(plainKey, options = {}) {
  ensureServerDirs();
  const keys = loadJson("keys.json", {});
  const hash = sha256(String(plainKey));
  const now = new Date().toISOString();
  const existing = keys[hash] || {};
  const plan = normalizePlan(options.plan ?? existing.plan);
  const uiBrand = normalizeUiBrand(options.ui_brand ?? existing.ui_brand);
  const trialDurationSeconds = normalizeTrialDurationSeconds(
    options.trial_duration_seconds ?? existing.trial_duration_seconds,
    7 * 24 * 60 * 60
  );
  const encrypted = options.encrypt === false
    ? existing.encrypted_key
    : (existing.encrypted_key || encryptLicenseKey(plainKey));
  const modelKeyB64 = normalizeModelKeyB64(options.model_key_b64) ||
    normalizeModelKeyB64(existing.model_key_b64) ||
    newModelKeyB64();
  const existingModelKeySource = String(existing.model_key_source || "").trim();
  keys[hash] = {
    disabled: Boolean(existing.disabled),
    plan,
    trial_duration_seconds: plan === "trial" ? trialDurationSeconds : 0,
    features: existing.features || options.features || DEFAULT_FEATURES,
    max_version: existing.max_version || options.max_version || "9999.99.99",
    ui_brand: uiBrand,
    created_at: existing.created_at || now,
    batch_id: existing.batch_id || options.batch_id || "",
    note: options.note ?? existing.note ?? "",
    owner_type: options.owner_type ?? existing.owner_type ?? "super",
    owner_username: options.owner_username ?? existing.owner_username ?? "",
    created_by: options.created_by ?? existing.created_by ?? (options.owner_username ? `xcsh:${options.owner_username}` : "super"),
    price_cents: normalizeCents(options.price_cents ?? existing.price_cents, 0),
    charged_cents: normalizeCents(options.charged_cents ?? existing.charged_cents, 0),
    model_key_b64: modelKeyB64,
    model_key_source: existingModelKeySource || (normalizeModelKeyB64(options.model_key_b64) ? "client" : "server"),
    encrypted_key: encrypted
  };
  saveJson("keys.json", keys);
  return { key: String(plainKey), hash, record: keys[hash] };
}

export function generateLicenseKeys(count, options = {}) {
  const batchId = options.batch_id || newBatchId();
  const keys = [];
  const targetCount = Math.max(1, Math.min(500, Number(count) || 1));
  const existing = loadJson("keys.json", {});
  while (keys.length < targetCount) {
    const key = generateLicenseKeyText();
    const hash = sha256(key);
    if (existing[hash] || keys.some((item) => item.hash === hash)) continue;
    keys.push(addLicenseKey(key, { ...options, batch_id: batchId }));
  }
  return { batch_id: batchId, keys };
}

export function readKeys() {
  return loadJson("keys.json", {});
}

export function writeKeys(keys) {
  saveJson("keys.json", keys);
}

export function ensureKeyModelKey(keys, hash, preferredModelKey = null) {
  const keyHash = String(hash || "");
  if (!keys || typeof keys !== "object" || !keyHash || !keys[keyHash]) {
    return { record: null, changed: false };
  }
  const record = keys[keyHash];
  const normalized = normalizeModelKeyB64(record.model_key_b64);
  const preferred = normalizeModelKeyInput(preferredModelKey);
  if (normalized) {
    if (record.model_key_b64 !== normalized) {
      record.model_key_b64 = normalized;
      return { record, changed: true };
    }
    return { record, changed: false };
  }
  record.model_key_b64 = preferred || newModelKeyB64();
  record.model_key_source = preferred ? "client" : "server";
  record.model_key_created_at = isoNow();
  return { record, changed: true };
}

export function modelKeyPackage(keyRecord) {
  const keyB64 = normalizeModelKeyB64(keyRecord?.model_key_b64);
  if (!keyB64) return null;
  const key = Buffer.from(keyB64, "base64");
  return {
    format: "aimk1",
    key_b64: keyB64,
    code: `AIMK1_${key.toString("base64url")}`,
    size: key.length,
    sha256: sha256(key)
  };
}

export function readActivations() {
  return loadJson("activations.json", {});
}

export function writeActivations(activations) {
  saveJson("activations.json", activations);
}

function normalizedDeviceFingerprint(value) {
  return String(value || "").trim();
}

function normalizedDeviceId(value) {
  return String(value || "").trim();
}

function activationFingerprintSet(activation) {
  const values = new Set();
  if (!activation || typeof activation !== "object") return values;
  for (const key of [
    "device_fingerprint_hash",
    "previous_device_fingerprint_hash",
    "legacy_device_fingerprint_hash",
    "fingerprint_hash"
  ]) {
    const value = normalizedDeviceFingerprint(activation[key]);
    if (value) values.add(value);
  }
  for (const key of ["migrated_fingerprints", "fingerprint_aliases"]) {
    if (!Array.isArray(activation[key])) continue;
    for (const item of activation[key]) {
      const value = normalizedDeviceFingerprint(item);
      if (value) values.add(value);
    }
  }
  return values;
}

function normalizeBlockedDeviceRecord(record, fallback = {}) {
  const source = record && typeof record === "object" ? record : {};
  return {
    device_fingerprint_hash: normalizedDeviceFingerprint(source.device_fingerprint_hash || fallback.device_fingerprint_hash),
    primary_device_fingerprint_hash: normalizedDeviceFingerprint(source.primary_device_fingerprint_hash || fallback.primary_device_fingerprint_hash),
    device_id: normalizedDeviceId(source.device_id || fallback.device_id),
    license_id: String(source.license_id || fallback.license_id || ""),
    source_key_hash: String(source.source_key_hash || fallback.source_key_hash || ""),
    reason: String(source.reason || fallback.reason || ""),
    blocked_at: String(source.blocked_at || fallback.blocked_at || ""),
    blocked_by: String(source.blocked_by || fallback.blocked_by || "")
  };
}

function normalizeBlockedDevices(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  const fingerprintsSource = source.fingerprints && typeof source.fingerprints === "object" ? source.fingerprints : {};
  const deviceIdsSource = source.device_ids && typeof source.device_ids === "object" ? source.device_ids : {};
  const fingerprints = {};
  const device_ids = {};
  for (const [fingerprint, record] of Object.entries(fingerprintsSource)) {
    const key = normalizedDeviceFingerprint(fingerprint);
    if (!key) continue;
    fingerprints[key] = normalizeBlockedDeviceRecord(record, { device_fingerprint_hash: key });
  }
  for (const [deviceId, record] of Object.entries(deviceIdsSource)) {
    const key = normalizedDeviceId(deviceId);
    if (!key) continue;
    device_ids[key] = normalizeBlockedDeviceRecord(record, { device_id: key });
  }
  return {
    fingerprints,
    device_ids,
    updated_at: String(source.updated_at || "")
  };
}

export function readBlockedDevices() {
  return normalizeBlockedDevices(loadJson("blocked_devices.json", {}));
}

export function writeBlockedDevices(blockedDevices) {
  saveJson("blocked_devices.json", normalizeBlockedDevices(blockedDevices));
}

export function findBlockedDevice(candidates, blockedDevices = readBlockedDevices()) {
  const source = candidates && typeof candidates === "object" ? candidates : {};
  const fingerprints = new Set();
  const addFingerprint = (value) => {
    const normalized = normalizedDeviceFingerprint(value);
    if (normalized) fingerprints.add(normalized);
  };
  addFingerprint(source.device_fingerprint_hash);
  if (Array.isArray(source.fingerprints)) {
    for (const item of source.fingerprints) addFingerprint(item);
  }
  const deviceId = normalizedDeviceId(source.device_id);
  const normalizedBlocked = normalizeBlockedDevices(blockedDevices);
  for (const fingerprint of fingerprints) {
    const record = normalizedBlocked.fingerprints[fingerprint];
    if (record) {
      return { ...record, match_type: "fingerprint", match_value: fingerprint };
    }
  }
  if (deviceId && normalizedBlocked.device_ids[deviceId]) {
    return { ...normalizedBlocked.device_ids[deviceId], match_type: "device_id", match_value: deviceId };
  }
  return null;
}

export function activationMatchesDevice(activation, fingerprints = [], deviceId = "") {
  if (!activation || typeof activation !== "object") return false;
  const targetFingerprints = new Set();
  for (const item of fingerprints || []) {
    const value = normalizedDeviceFingerprint(item);
    if (value) targetFingerprints.add(value);
  }
  const activationFingerprints = activationFingerprintSet(activation);
  for (const fingerprint of targetFingerprints) {
    if (activationFingerprints.has(fingerprint)) return true;
  }
  const normalizedTargetDeviceId = normalizedDeviceId(deviceId);
  const activationDeviceId = normalizedDeviceId(activation.device_id);
  return Boolean(normalizedTargetDeviceId && activationDeviceId && normalizedTargetDeviceId === activationDeviceId);
}

export function findFrozenActivationForDevice(activations, candidates) {
  const source = candidates && typeof candidates === "object" ? candidates : {};
  const fingerprints = Array.isArray(source.fingerprints) ? source.fingerprints : [source.device_fingerprint_hash];
  const deviceId = source.device_id || "";
  for (const [keyHash, activation] of Object.entries(activations || {})) {
    if (activation?.frozen && activationMatchesDevice(activation, fingerprints, deviceId)) {
      return { keyHash, activation };
    }
  }
  return null;
}

function blankAnnouncement() {
  return {
    enabled: false,
    title: "",
    content: "",
    version: "",
    updated_at: ""
  };
}

function normalizeAnnouncementRecord(payload) {
  const source = payload && typeof payload === "object" ? payload : {};
  return {
    enabled: Boolean(source.enabled),
    title: String(source.title || ""),
    content: String(source.content || ""),
    version: String(source.version || ""),
    updated_at: String(source.updated_at || "")
  };
}

export function readAnnouncements() {
  const raw = loadJson("announcement.json", blankAnnouncement());
  const empty = blankAnnouncement();
  if (raw && typeof raw === "object" && raw.brands && typeof raw.brands === "object") {
    return {
      brands: {
        yu: normalizeAnnouncementRecord(raw.brands.yu || empty),
        xh: normalizeAnnouncementRecord(raw.brands.xh || empty),
        xcsh: normalizeAnnouncementRecord(raw.brands.xcsh || empty)
      },
      updated_at: String(raw.updated_at || "")
    };
  }
  return {
    brands: {
      yu: normalizeAnnouncementRecord(raw),
      xh: empty,
      xcsh: empty
    },
    updated_at: String(raw?.updated_at || "")
  };
}

export function readAnnouncement(brand = "yu") {
  const announcements = readAnnouncements();
  const uiBrand = normalizeUiBrand(brand);
  return normalizeAnnouncementRecord(announcements.brands[uiBrand] || blankAnnouncement());
}

export function writeAnnouncement(payload, brand = "yu") {
  const now = new Date().toISOString();
  const title = String(payload?.title || "").trim().slice(0, 80);
  const content = String(payload?.content || "").trim().slice(0, 4000);
  const version = String(payload?.version || "").trim().slice(0, 64) || now.replace(/[-:.TZ]/g, "").slice(0, 14);
  const uiBrand = normalizeUiBrand(brand);
  const announcements = readAnnouncements();
  const announcement = {
    enabled: Boolean(payload?.enabled),
    title,
    content,
    version,
    updated_at: now
  };
  announcements.brands[uiBrand] = announcement;
  announcements.updated_at = now;
  saveJson("announcement.json", announcements);
  return announcement;
}

export function setKeyDisabled(hash, disabled, reason = "") {
  const keys = readKeys();
  if (!keys[hash]) {
    throw new Error("license key not found");
  }
  keys[hash].disabled = Boolean(disabled);
  if (disabled) {
    keys[hash].disabled_at = new Date().toISOString();
    keys[hash].disabled_reason = reason || "";
  } else {
    delete keys[hash].disabled_at;
    delete keys[hash].disabled_reason;
  }
  writeKeys(keys);
  return keys[hash];
}

export function deleteLicenseKey(hashValue) {
  const hash = String(hashValue || "");
  const keys = readKeys();
  const existing = keys[hash];
  if (!existing) {
    throw new Error("license key not found");
  }
  delete keys[hash];
  writeKeys(keys);

  const activations = readActivations();
  const activation = activations[hash] || null;
  if (activation) {
    delete activations[hash];
    writeActivations(activations);
  }

  const blockedDevices = readBlockedDevices();
  let blockedChanged = false;
  for (const [fingerprint, record] of Object.entries(blockedDevices.fingerprints || {})) {
    if (String(record?.source_key_hash || "") !== hash) continue;
    delete blockedDevices.fingerprints[fingerprint];
    blockedChanged = true;
  }
  for (const [deviceId, record] of Object.entries(blockedDevices.device_ids || {})) {
    if (String(record?.source_key_hash || "") !== hash) continue;
    delete blockedDevices.device_ids[deviceId];
    blockedChanged = true;
  }
  if (blockedChanged) {
    blockedDevices.updated_at = isoNow();
    writeBlockedDevices(blockedDevices);
  }

  return { key: existing, activation, blocked_entries_removed: blockedChanged };
}

export function setActivationFrozen(keyHash, frozen, reason = "") {
  const activations = readActivations();
  if (!activations[keyHash]) {
    throw new Error("activation not found");
  }
  activations[keyHash].frozen = Boolean(frozen);
  if (frozen) {
    activations[keyHash].frozen_at = new Date().toISOString();
    activations[keyHash].frozen_reason = reason || "";
  } else {
    delete activations[keyHash].frozen_at;
    delete activations[keyHash].frozen_reason;
    delete activations[keyHash].revoked_at;
  }
  writeActivations(activations);
  return activations[keyHash];
}

export function setDeviceBlockedFromActivation(keyHash, blocked, reason = "", blockedBy = "") {
  const activations = readActivations();
  const activation = activations[String(keyHash)];
  if (!activation) {
    throw new Error("activation not found");
  }
  const fingerprints = activationFingerprintSet(activation);
  const fingerprintList = [...fingerprints];
  const deviceId = normalizedDeviceId(activation.device_id);
  if (!fingerprints.size && !deviceId) {
    throw new Error("activation has no device identity");
  }

  const blockedDevices = readBlockedDevices();
  const now = new Date().toISOString();
  if (blocked) {
    const primaryFingerprint = normalizedDeviceFingerprint(activation.device_fingerprint_hash) || [...fingerprints][0] || "";
    const record = normalizeBlockedDeviceRecord({
      device_fingerprint_hash: primaryFingerprint,
      primary_device_fingerprint_hash: primaryFingerprint,
      device_id: deviceId,
      license_id: activation.license_id || "",
      source_key_hash: String(keyHash),
      reason,
      blocked_at: now,
      blocked_by: blockedBy
    });
    for (const fingerprint of fingerprints) {
      blockedDevices.fingerprints[fingerprint] = {
        ...record,
        device_fingerprint_hash: fingerprint,
        primary_device_fingerprint_hash: primaryFingerprint
      };
    }
    if (deviceId) {
      blockedDevices.device_ids[deviceId] = record;
    }
    blockedDevices.updated_at = now;
    writeBlockedDevices(blockedDevices);
    for (const item of Object.values(activations || {})) {
      if (!activationMatchesDevice(item, fingerprintList, deviceId)) continue;
      item.frozen = true;
      item.frozen_at = now;
      item.frozen_reason = reason || "";
    }
    writeActivations(activations);
    return record;
  }

  const existingBlock = findBlockedDevice({
    device_id: deviceId,
    device_fingerprint_hash: activation.device_fingerprint_hash,
    fingerprints: fingerprintList
  }, blockedDevices);
  if (
    existingBlock &&
    String(blockedBy || "").startsWith("xcsh:") &&
    existingBlock.blocked_by &&
    existingBlock.blocked_by !== blockedBy
  ) {
    throw new Error("device was frozen by another administrator");
  }

  for (const fingerprint of fingerprints) {
    delete blockedDevices.fingerprints[fingerprint];
  }
  if (deviceId) {
    delete blockedDevices.device_ids[deviceId];
  }
  blockedDevices.updated_at = now;
  writeBlockedDevices(blockedDevices);
  for (const item of Object.values(activations || {})) {
    if (!activationMatchesDevice(item, fingerprintList, deviceId)) continue;
    delete item.frozen;
    delete item.frozen_at;
    delete item.frozen_reason;
    delete item.revoked_at;
  }
  writeActivations(activations);
  return activation;
}

export function unblockDeviceByIdentity(candidates = {}) {
  const source = candidates && typeof candidates === "object" ? candidates : {};
  const fingerprints = new Set();
  const deviceIds = new Set();
  const licenseIds = new Set();
  const sourceKeyHashes = new Set();

  const addFingerprint = (value) => {
    const fingerprint = normalizedDeviceFingerprint(value);
    if (fingerprint) fingerprints.add(fingerprint);
  };
  const addDeviceId = (value) => {
    const deviceId = normalizedDeviceId(value);
    if (deviceId) deviceIds.add(deviceId);
  };
  const addLicenseId = (value) => {
    const licenseId = String(value || "").trim();
    if (licenseId) licenseIds.add(licenseId);
  };
  const addSourceKeyHash = (value) => {
    const keyHash = String(value || "").trim();
    if (keyHash) sourceKeyHashes.add(keyHash);
  };

  addFingerprint(source.device_fingerprint_hash);
  addFingerprint(source.primary_device_fingerprint_hash);
  if (Array.isArray(source.fingerprints)) {
    for (const item of source.fingerprints) addFingerprint(item);
  }
  addDeviceId(source.device_id);
  addLicenseId(source.license_id);
  addSourceKeyHash(source.source_key_hash || source.key_hash || source.activation_hash);

  if (!fingerprints.size && !deviceIds.size && !licenseIds.size && !sourceKeyHashes.size) {
    throw new Error("device identity is required");
  }

  const activations = readActivations();
  const addActivationIdentity = (activation) => {
    if (!activation || typeof activation !== "object") return;
    for (const fingerprint of activationFingerprintSet(activation)) addFingerprint(fingerprint);
    addDeviceId(activation.device_id);
    addLicenseId(activation.license_id);
  };

  for (const keyHash of [...sourceKeyHashes]) {
    addActivationIdentity(activations[keyHash]);
  }
  if (licenseIds.size) {
    for (const activation of Object.values(activations || {})) {
      if (licenseIds.has(String(activation?.license_id || "").trim())) {
        addActivationIdentity(activation);
      }
    }
  }

  const blockedDevices = readBlockedDevices();
  const blockedRecords = [
    ...Object.values(blockedDevices.fingerprints || {}),
    ...Object.values(blockedDevices.device_ids || {})
  ];

  const recordMatchesKnownIdentity = (record) => {
    const recordFingerprints = [
      record?.device_fingerprint_hash,
      record?.primary_device_fingerprint_hash
    ].map(normalizedDeviceFingerprint).filter(Boolean);
    if (recordFingerprints.some((fingerprint) => fingerprints.has(fingerprint))) return true;
    const recordDeviceId = normalizedDeviceId(record?.device_id);
    if (recordDeviceId && deviceIds.has(recordDeviceId)) return true;
    const recordLicenseId = String(record?.license_id || "").trim();
    if (recordLicenseId && licenseIds.has(recordLicenseId)) return true;
    const recordSourceKeyHash = String(record?.source_key_hash || "").trim();
    return Boolean(recordSourceKeyHash && sourceKeyHashes.has(recordSourceKeyHash));
  };

  let expanded = true;
  while (expanded) {
    expanded = false;
    for (const record of blockedRecords) {
      if (!recordMatchesKnownIdentity(record)) continue;
      const before = fingerprints.size + deviceIds.size + licenseIds.size + sourceKeyHashes.size;
      addFingerprint(record.device_fingerprint_hash);
      addFingerprint(record.primary_device_fingerprint_hash);
      addDeviceId(record.device_id);
      addLicenseId(record.license_id);
      addSourceKeyHash(record.source_key_hash);
      for (const keyHash of [...sourceKeyHashes]) addActivationIdentity(activations[keyHash]);
      if (fingerprints.size + deviceIds.size + licenseIds.size + sourceKeyHashes.size > before) {
        expanded = true;
      }
    }
  }

  let blockedEntriesRemoved = 0;
  for (const [fingerprint, record] of Object.entries(blockedDevices.fingerprints || {})) {
    if (!recordMatchesKnownIdentity(record) && !fingerprints.has(normalizedDeviceFingerprint(fingerprint))) continue;
    delete blockedDevices.fingerprints[fingerprint];
    blockedEntriesRemoved += 1;
  }
  for (const [deviceId, record] of Object.entries(blockedDevices.device_ids || {})) {
    if (!recordMatchesKnownIdentity(record) && !deviceIds.has(normalizedDeviceId(deviceId))) continue;
    delete blockedDevices.device_ids[deviceId];
    blockedEntriesRemoved += 1;
  }

  if (blockedEntriesRemoved) {
    blockedDevices.updated_at = isoNow();
    writeBlockedDevices(blockedDevices);
  }

  let activationsUnfrozen = 0;
  for (const [keyHash, activation] of Object.entries(activations || {})) {
    if (!activation?.frozen) continue;
    const matchesHash = sourceKeyHashes.has(String(keyHash));
    const matchesLicense = licenseIds.has(String(activation.license_id || "").trim());
    const matchesDeviceId = deviceIds.has(normalizedDeviceId(activation.device_id));
    const matchesFingerprint = activationMatchesDevice(activation, [...fingerprints], "");
    if (!matchesHash && !matchesLicense && !matchesDeviceId && !matchesFingerprint) continue;
    delete activation.frozen;
    delete activation.frozen_at;
    delete activation.frozen_reason;
    delete activation.revoked_at;
    activationsUnfrozen += 1;
  }
  if (activationsUnfrozen) {
    writeActivations(activations);
  }

  return {
    blocked_entries_removed: blockedEntriesRemoved,
    activations_unfrozen: activationsUnfrozen
  };
}

export function resetActivation(keyHash) {
  const activations = readActivations();
  if (!activations[keyHash]) {
    throw new Error("activation not found");
  }
  const previous = activations[keyHash];
  delete activations[keyHash];
  writeActivations(activations);
  return previous;
}

export function findActivationByLicense(activations, license, deviceFingerprintHash) {
  const licenseId = String(license?.license_id || "");
  for (const [keyHash, activation] of Object.entries(activations || {})) {
    if (!activation) continue;
    if (
      activation.license_id === licenseId &&
      activation.device_fingerprint_hash === deviceFingerprintHash
    ) {
      return { keyHash, activation };
    }
  }
  return null;
}

export function revocationReason(keyRecord, activation) {
  if (activation?.frozen) {
    return activation.frozen_reason || "device is frozen";
  }
  if (keyRecord?.disabled) {
    return keyRecord.disabled_reason || "license key is disabled";
  }
  const plan = normalizePlan(activation?.plan || keyRecord?.plan);
  if (plan === "trial" && activation?.expires_at && isExpiredIso(activation.expires_at)) {
    return "trial license expired";
  }
  return "";
}

export function markActivationRevoked(keyHash) {
  const activations = readActivations();
  if (activations[keyHash]) {
    activations[keyHash].revoked_at = new Date().toISOString();
    writeActivations(activations);
  }
}

export function readOrCreateCoreSecret() {
  if (fs.existsSync(CORE_SECRET_PATH)) {
    return fs.readFileSync(CORE_SECRET_PATH);
  }
  const secret = crypto.randomBytes(32);
  fs.mkdirSync(path.dirname(CORE_SECRET_PATH), { recursive: true });
  fs.writeFileSync(CORE_SECRET_PATH, secret, { mode: 0o600 });
  return secret;
}

export function readOrCreateUsbProxySecret() {
  if (fs.existsSync(USB_PROXY_SECRET_PATH)) {
    return fs.readFileSync(USB_PROXY_SECRET_PATH);
  }
  const secret = crypto.randomBytes(32);
  fs.mkdirSync(path.dirname(USB_PROXY_SECRET_PATH), { recursive: true });
  fs.writeFileSync(USB_PROXY_SECRET_PATH, secret, { mode: 0o600 });
  return secret;
}

export function corePlainPath(version = CORE_VERSION) {
  const releaseVersion = String(version || CORE_VERSION || "local").trim() || "local";
  return path.join(CORE_RELEASE_DIR, releaseVersion, "libai_core.so");
}

export function findCorePlainPath(version = "") {
  const requested = String(version || CORE_VERSION || "local").trim() || "local";
  const explicit = String(version || "").trim() !== "";
  const preferred = corePlainPath(requested);
  if (fs.existsSync(preferred)) return preferred;
  const local = path.join(CORE_RELEASE_DIR, "local", "libai_core.so");
  if ((!explicit || requested === "local") && fs.existsSync(local)) return local;
  return "";
}

export function usbProxyPlainPath(version = USB_PROXY_VERSION) {
  const releaseVersion = String(version || USB_PROXY_VERSION || "local").trim() || "local";
  return path.join(USB_PROXY_RELEASE_DIR, releaseVersion, "usb-proxy");
}

export function findUsbProxyPlainPath(version = "") {
  const requested = String(version || USB_PROXY_VERSION || "local").trim() || "local";
  const explicitVersion = String(version || "").trim() !== "";
  const explicitPath = String(process.env.AIASSISTANCE_USB_PROXY_PLAIN || "").trim();
  if (!explicitVersion && explicitPath && fs.existsSync(explicitPath)) return path.resolve(explicitPath);
  const preferred = usbProxyPlainPath(requested);
  if (fs.existsSync(preferred)) return preferred;
  const local = path.join(USB_PROXY_RELEASE_DIR, "local", "usb-proxy");
  if ((!explicitVersion || requested === "local") && fs.existsSync(local)) return local;
  if (explicitVersion) return "";
  const siblingBuild = path.resolve(ROOT, "..", "..", "usb-proxy-main", "build", "aarch64", "usb-proxy");
  if (fs.existsSync(siblingBuild)) return siblingBuild;
  return "";
}

export function coreAad({ license_id, device_id, device_fingerprint_hash, version }) {
  return stableJson({
    license_id,
    device_id,
    device_fingerprint_hash,
    core_version: version
  });
}

export function usbProxyAad({ license_id, device_id, device_fingerprint_hash, version }) {
  return stableJson({
    license_id,
    device_id,
    device_fingerprint_hash,
    usb_proxy_version: version
  });
}

export function buildSignedLicense({
  existing,
  activation,
  keyRecord,
  deviceId,
  deviceFingerprintHash,
  now = isoNow()
}) {
  const plan = normalizePlan(activation?.plan || keyRecord?.plan);
  const license = {
    license_id: existing?.license_id || activation?.license_id || `lic_${crypto.randomBytes(8).toString("hex")}`,
    device_id: deviceId,
    device_fingerprint_hash: deviceFingerprintHash,
    issued_at: existing?.issued_at || activation?.issued_at || now,
    plan,
    features: keyRecord.features || DEFAULT_FEATURES,
    max_version: keyRecord.max_version || "9999.99.99",
    ui_brand: normalizeUiBrand(keyRecord.ui_brand),
    license_version: "2"
  };
  if (plan === "trial") {
    license.expires_at = activation?.expires_at || addSecondsIso(now, normalizeTrialDurationSeconds(keyRecord.trial_duration_seconds));
    license.online_required = true;
    license.check_interval_seconds = TRIAL_CHECK_INTERVAL_SECONDS;
    license.grace_seconds = TRIAL_GRACE_SECONDS;
  }
  license.signature_algorithm = "Ed25519";
  license.signature = signLicense(license);
  return license;
}

export function buildOnlineGrant({ license, activation, now = isoNow() }) {
  const grant = {
    type: "aiassistance_online_grant",
    license_id: license.license_id,
    device_id: license.device_id,
    device_fingerprint_hash: license.device_fingerprint_hash,
    plan: "trial",
    issued_at: now,
    valid_until: addSecondsIso(now, TRIAL_GRANT_VALID_SECONDS),
    expires_at: activation?.expires_at || license.expires_at || "",
    check_interval_seconds: TRIAL_CHECK_INTERVAL_SECONDS,
    signature_algorithm: "Ed25519"
  };
  grant.signature = signLicense(grant);
  return grant;
}

export function buildCorePackage(license, publicCoreUrl, releaseVersion = "") {
  cleanupIssuedPackages();
  const plainPath = findCorePlainPath(releaseVersion);
  if (!plainPath) {
    return { mode: "missing", note: "core release is not configured on this server" };
  }
  const version = path.basename(path.dirname(plainPath));
  const plaintext = fs.readFileSync(plainPath);
  const salt = crypto.randomBytes(16);
  const nonce = crypto.randomBytes(12);
  const secret = readOrCreateCoreSecret();
  const key = crypto.hkdfSync(
    "sha256",
    secret,
    salt,
    Buffer.from(`${license.license_id}:${license.device_fingerprint_hash}:${version}`),
    32
  );
  const keyBuffer = Buffer.from(key);
  const aad = coreAad({
    license_id: license.license_id,
    device_id: license.device_id,
    device_fingerprint_hash: license.device_fingerprint_hash,
    version
  });
  const cipher = crypto.createCipheriv("aes-256-gcm", keyBuffer, nonce);
  cipher.setAAD(Buffer.from(aad));
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();
  const token = crypto.randomBytes(24).toString("hex");
  const fileName = `${token}.enc`;
  const filePath = path.join(CORE_PACKAGE_DIR, fileName);
  fs.mkdirSync(CORE_PACKAGE_DIR, { recursive: true });
  fs.writeFileSync(filePath, ciphertext, { mode: 0o600 });
  const tokens = loadJson("core-tokens.json", {});
  tokens[token] = {
    file: fileName,
    license_id: license.license_id,
    device_id: license.device_id,
    device_fingerprint_hash: license.device_fingerprint_hash,
    version,
    created_at: new Date().toISOString(),
    expires_at_ms: Date.now() + ISSUED_PACKAGE_TTL_MS
  };
  saveJson("core-tokens.json", tokens);
  return {
    mode: "download",
    format: "aicore1",
    version,
    download_url: publicCoreUrl(token),
    sha256: sha256Buffer(ciphertext),
    size: ciphertext.length,
    key_b64: keyBuffer.toString("base64"),
    nonce_b64: nonce.toString("base64"),
    tag_b64: tag.toString("base64"),
    salt_b64: salt.toString("base64"),
    aad
  };
}

export function buildUsbProxyPackage(license, publicUsbProxyUrl, releaseVersion = "") {
  cleanupIssuedPackages();
  const plainPath = findUsbProxyPlainPath(releaseVersion);
  if (!plainPath) {
    return { mode: "missing", note: "usb-proxy release is not configured on this server" };
  }
  const parentVersion = path.basename(path.dirname(plainPath));
  const version = parentVersion && parentVersion !== "aarch64" ? parentVersion : USB_PROXY_VERSION;
  const plaintext = fs.readFileSync(plainPath);
  const salt = crypto.randomBytes(16);
  const nonce = crypto.randomBytes(12);
  const secret = readOrCreateUsbProxySecret();
  const key = crypto.hkdfSync(
    "sha256",
    secret,
    salt,
    Buffer.from(`${license.license_id}:${license.device_fingerprint_hash}:usb-proxy:${version}`),
    32
  );
  const keyBuffer = Buffer.from(key);
  const aad = usbProxyAad({
    license_id: license.license_id,
    device_id: license.device_id,
    device_fingerprint_hash: license.device_fingerprint_hash,
    version
  });
  const cipher = crypto.createCipheriv("aes-256-gcm", keyBuffer, nonce);
  cipher.setAAD(Buffer.from(aad));
  const ciphertext = Buffer.concat([cipher.update(plaintext), cipher.final()]);
  const tag = cipher.getAuthTag();
  const token = crypto.randomBytes(24).toString("hex");
  const fileName = `${token}.enc`;
  const filePath = path.join(USB_PROXY_PACKAGE_DIR, fileName);
  fs.mkdirSync(USB_PROXY_PACKAGE_DIR, { recursive: true });
  fs.writeFileSync(filePath, ciphertext, { mode: 0o600 });
  const tokens = loadJson("usb-proxy-tokens.json", {});
  tokens[token] = {
    file: fileName,
    license_id: license.license_id,
    device_id: license.device_id,
    device_fingerprint_hash: license.device_fingerprint_hash,
    version,
    created_at: new Date().toISOString(),
    expires_at_ms: Date.now() + ISSUED_PACKAGE_TTL_MS
  };
  saveJson("usb-proxy-tokens.json", tokens);
  return {
    mode: "download",
    format: "aiusbproxy1",
    version,
    download_url: publicUsbProxyUrl(token),
    sha256: sha256Buffer(ciphertext),
    size: ciphertext.length,
    key_b64: keyBuffer.toString("base64"),
    nonce_b64: nonce.toString("base64"),
    tag_b64: tag.toString("base64"),
    salt_b64: salt.toString("base64"),
    aad
  };
}

function safeHailoPart(value, fallback = "") {
  return String(value || fallback || "").trim().replace(/[^A-Za-z0-9._-]+/g, "-").replace(/^-+|-+$/g, "");
}

function collectHailoManifestCandidates(dir, depth = 0, maxDepth = 2) {
  if (!fs.existsSync(dir) || !fs.statSync(dir).isDirectory()) {
    return [];
  }
  const results = [];
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const fullPath = path.join(dir, entry.name);
    if (entry.isFile() && entry.name.endsWith(".json")) {
      results.push(fullPath);
    } else if (entry.isDirectory() && depth < maxDepth) {
      results.push(...collectHailoManifestCandidates(fullPath, depth + 1, maxDepth));
    }
  }
  return results;
}

function findHailoPackageManifest(releaseVersion = HAILO_VERSION, kernelRelease = HAILO_KERNEL_RELEASE, boardId = "") {
  const requestedVersion = String(releaseVersion || HAILO_VERSION || "").trim();
  const requestedKernel = String(kernelRelease || HAILO_KERNEL_RELEASE || "").trim();
  const requestedBoard = safeHailoPart(boardId);
  const candidates = [];
  const addCandidate = (manifestPath) => {
    if (manifestPath && !candidates.includes(manifestPath)) {
      candidates.push(manifestPath);
    }
  };
  const boardCandidates = requestedBoard ? [requestedBoard] : ["orangepi"];

  for (const board of boardCandidates) {
    for (const extension of [".tar.zst.json", ".tar.gz.json"]) {
      addCandidate(path.join(
        HAILO_RELEASE_DIR,
        requestedVersion,
        `hailo-${board}-${requestedKernel}-${requestedVersion}${extension}`
      ));
      addCandidate(path.join(
        HAILO_RELEASE_DIR,
        `hailo-${board}-${requestedKernel}-${requestedVersion}${extension}`
      ));
    }
  }

  if (fs.existsSync(HAILO_RELEASE_DIR)) {
    for (const manifestPath of collectHailoManifestCandidates(HAILO_RELEASE_DIR)) {
      addCandidate(manifestPath);
    }
  }

  for (const manifestPath of candidates) {
    if (!fs.existsSync(manifestPath)) continue;
    let manifest = null;
    try {
      manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    } catch {
      continue;
    }
    if (!manifest || typeof manifest !== "object") continue;
    const version = String(manifest.version || "").trim();
    const kernel = String(manifest.kernel_release || "").trim();
    const manifestBoard = safeHailoPart(manifest.board_id);
    const fileName = path.basename(manifest.file || "");
    if (!version || !kernel || !fileName) continue;
    if (requestedVersion && version !== requestedVersion) continue;
    if (requestedKernel && kernel !== requestedKernel) continue;
    if (requestedBoard) {
      if (manifestBoard && manifestBoard !== requestedBoard) continue;
      if (!manifestBoard && requestedBoard !== "orangepi") continue;
    }
    const packagePath = path.resolve(path.dirname(manifestPath), fileName);
    if (!fs.existsSync(packagePath) || !fs.statSync(packagePath).isFile()) continue;
    return { manifest, manifestPath, packagePath };
  }
  return null;
}

export function buildHailoPackage(license, publicHailoUrl, releaseVersion = HAILO_VERSION, kernelRelease = HAILO_KERNEL_RELEASE, boardId = "") {
  cleanupIssuedPackages();
  const found = findHailoPackageManifest(releaseVersion, kernelRelease, boardId);
  if (!found) {
    return { mode: "missing", note: "Hailo dependency release is not configured on this server" };
  }
  const version = String(found.manifest.version || releaseVersion || HAILO_VERSION).trim();
  const resolvedKernelRelease = String(found.manifest.kernel_release || kernelRelease || HAILO_KERNEL_RELEASE).trim();
  const resolvedBoardId = safeHailoPart(found.manifest.board_id || boardId || "orangepi", "orangepi");
  const packageBytes = fs.readFileSync(found.packagePath);
  const expectedSha = String(found.manifest.sha256 || sha256Buffer(packageBytes)).trim().toLowerCase();
  const token = crypto.randomBytes(24).toString("hex");
  const tokens = loadJson("hailo-tokens.json", {});
  tokens[token] = {
    file: path.basename(found.packagePath),
    source_path: found.packagePath,
    license_id: license.license_id,
    device_id: license.device_id,
    device_fingerprint_hash: license.device_fingerprint_hash,
    version,
    board_id: resolvedBoardId,
    kernel_release: resolvedKernelRelease,
    created_at: new Date().toISOString(),
    expires_at_ms: Date.now() + ISSUED_PACKAGE_TTL_MS
  };
  saveJson("hailo-tokens.json", tokens);
  return {
    mode: "download",
    format: "aihailo1",
    version,
    board_id: resolvedBoardId,
    kernel_release: resolvedKernelRelease,
    download_url: publicHailoUrl(token),
    sha256: expectedSha,
    size: Number(found.manifest.size || packageBytes.length),
    file: path.basename(found.packagePath)
  };
}

function publicPackageFromManifest(manifest, publicPackageUrl) {
  if (!manifest || typeof manifest !== "object") return null;
  const fileName = path.basename(manifest.file || "");
  const version = String(manifest.version || "").trim();
  if (!fileName || !version) return null;
  const filePath = path.join(PACKAGE_DIR, fileName);
  const result = {
    type: manifest.type || "full",
    version,
    file: fileName,
    core_version: manifest.core_version || "",
    usb_proxy_version: manifest.usb_proxy_version || "",
    signature: manifest.signature || ""
  };
  if (fileName && fs.existsSync(filePath) && fs.statSync(filePath).isFile()) {
    const stat = fs.statSync(filePath);
    result.url = publicPackageUrl(fileName);
    result.sha256 = manifest.sha256 || sha256Buffer(fs.readFileSync(filePath));
    result.size = stat.size;
  }
  return result;
}

export function loadLatestPackage(publicPackageUrl) {
  const manifest = loadJson("latest-package.json", null);
  return publicPackageFromManifest(manifest, publicPackageUrl);
}

export function listUpdatePackages(publicPackageUrl) {
  if (!fs.existsSync(PACKAGE_DIR)) return [];
  const packages = [];
  for (const name of fs.readdirSync(PACKAGE_DIR)) {
    if (!name.endsWith(".json")) continue;
    const manifestPath = path.join(PACKAGE_DIR, name);
    let manifest = null;
    try {
      manifest = JSON.parse(fs.readFileSync(manifestPath, "utf8"));
    } catch {
      continue;
    }
    const record = publicPackageFromManifest(manifest, publicPackageUrl);
    if (record && record.url) {
      packages.push(record);
    }
  }
  packages.sort((left, right) => (
    compareVersionText(right.version, left.version) ||
    String(left.type || "").localeCompare(String(right.type || "")) ||
    String(left.file || "").localeCompare(String(right.file || ""))
  ));
  return packages;
}

export function loadPackageByVersion(publicPackageUrl, version) {
  const requested = String(version || "").trim();
  if (!requested) return null;
  const matches = listUpdatePackages(publicPackageUrl)
    .filter((item) => item.version === requested);
  return matches.find((item) => item.type === "full") || matches[0] || null;
}
