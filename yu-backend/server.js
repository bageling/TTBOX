import fs from "node:fs";
import net from "node:net";
import path from "node:path";
import { fileURLToPath } from "node:url";
import express from "express";

const SERVER_DIR = path.dirname(fileURLToPath(import.meta.url));

loadEnvFiles();

const {
  CORE_VERSION,
  HAILO_KERNEL_RELEASE,
  HAILO_VERSION,
  USB_PROXY_VERSION,
  addSecondsIso,
  buildOnlineGrant,
  buildHailoPackage,
  buildUsbProxyPackage,
  buildSignedLicense,
  PACKAGE_DIR,
  buildCorePackage,
  compareVersionText,
  cleanupIssuedPackages,
  consumeIssuedPackage,
  ensureServerDirs,
  ensureKeyModelKey,
  findBlockedDevice,
  findFrozenActivationForDevice,
  findActivationByLicense,
  isoNow,
  listUpdatePackages,
  loadJson,
  loadLatestPackage,
  loadPackageByVersion,
  markActivationRevoked,
  normalizePlan,
  normalizeTrialDurationSeconds,
  normalizeUiBrand,
  modelKeyPackage,
  readAnnouncement,
  readActivations,
  readKeys,
  requirePrivateKey,
  resolveIssuedPackage,
  revocationReason,
  sha256,
  stableJson,
  writeActivations,
  writeKeys
} = await import("./lib/store.js");

const PUBLIC_BASE_URL = (process.env.AIASSISTANCE_PUBLIC_BASE_URL || "").replace(/\/+$/, "");
const XCSH_MIN_UPDATE_VERSION = "2026.06.29.1";
const PORT = Number(process.env.PORT || process.env.AIASSISTANCE_PORT || 3020);
const TRUST_PROXY = parseTrustProxy(process.env.AIASSISTANCE_TRUST_PROXY || "loopback");
const ACTIVATE_RATE_WINDOW_MS = positiveIntegerEnv("AIASSISTANCE_ACTIVATE_RATE_WINDOW_MS", 10 * 60 * 1000);
const ACTIVATE_RATE_MAX_IP = positiveIntegerEnv("AIASSISTANCE_ACTIVATE_RATE_MAX_IP", 30);
const ACTIVATE_RATE_MAX_LICENSE = positiveIntegerEnv("AIASSISTANCE_ACTIVATE_RATE_MAX_LICENSE", 10);
const ACTIVATE_RATE_MAX_DEVICE = positiveIntegerEnv("AIASSISTANCE_ACTIVATE_RATE_MAX_DEVICE", 10);
const IP_LOCATION_LOOKUP_ENABLED = booleanEnv("AIASSISTANCE_IP_LOCATION_LOOKUP", true);
const IP_LOCATION_LOOKUP_TIMEOUT_MS = positiveIntegerEnv("AIASSISTANCE_IP_LOCATION_LOOKUP_TIMEOUT_MS", 1500);
const IP_LOCATION_CACHE_MS = positiveIntegerEnv("AIASSISTANCE_IP_LOCATION_CACHE_MS", 7 * 24 * 60 * 60 * 1000);
const IP_LOCATION_ENDPOINT = String(
  process.env.AIASSISTANCE_IP_LOCATION_ENDPOINT ||
  "http://ip-api.com/json/{ip}?fields=status,message,country,regionName,city,isp,query&lang=zh-CN"
);

ensureServerDirs();
cleanupIssuedPackages();
setInterval(() => {
  try {
    cleanupIssuedPackages();
  } catch (error) {
    console.warn(`issued package cleanup failed: ${error.message || error}`);
  }
}, 10 * 60 * 1000).unref();

const app = express();
app.set("trust proxy", TRUST_PROXY);
app.use(express.json({ limit: "1mb" }));

const router = express.Router();
const activateRateBuckets = new Map();
const ipLocationCache = new Map();
const pendingIpLocationLookups = new Map();
let nextRateCleanupAt = Date.now() + ACTIVATE_RATE_WINDOW_MS;

function loadEnvFiles() {
  const candidates = [];
  if (process.env.AIASSISTANCE_ENV_FILE) {
    candidates.push(path.resolve(process.env.AIASSISTANCE_ENV_FILE));
  }
  candidates.push(path.join(SERVER_DIR, ".env"));
  candidates.push(path.join(SERVER_DIR, ".evn"));

  const loaded = new Set();
  for (const envPath of candidates) {
    if (loaded.has(envPath) || !fs.existsSync(envPath)) continue;
    loaded.add(envPath);
    loadEnvFile(envPath);
  }
}

function loadEnvFile(envPath) {
  const text = fs.readFileSync(envPath, "utf8");
  for (const rawLine of text.split(/\r?\n/)) {
    let line = rawLine.trim();
    if (!line || line.startsWith("#")) continue;
    if (line.startsWith("export ")) {
      line = line.slice("export ".length).trim();
    }
    const equalsIndex = line.indexOf("=");
    if (equalsIndex <= 0) continue;
    const key = line.slice(0, equalsIndex).trim();
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) || process.env[key] !== undefined) continue;

    let value = line.slice(equalsIndex + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith("'") && value.endsWith("'"))
    ) {
      value = value.slice(1, -1);
    } else {
      const commentIndex = value.search(/\s#/);
      if (commentIndex >= 0) {
        value = value.slice(0, commentIndex).trimEnd();
      }
    }
    process.env[key] = value;
  }
}

function positiveIntegerEnv(name, fallback) {
  const value = Number(process.env[name]);
  return Number.isFinite(value) && value > 0 ? Math.floor(value) : fallback;
}

function booleanEnv(name, fallback) {
  const value = process.env[name];
  if (value === undefined || value === "") return fallback;
  const normalized = String(value).trim().toLowerCase();
  if (["1", "true", "yes", "on"].includes(normalized)) return true;
  if (["0", "false", "no", "off"].includes(normalized)) return false;
  return fallback;
}

function parseTrustProxy(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (["0", "false", "off", "no"].includes(normalized)) return false;
  if (["1", "true", "on", "yes"].includes(normalized)) return 1;
  return value;
}

function publicPackageUrl(fileName) {
  if (PUBLIC_BASE_URL) {
    return `${PUBLIC_BASE_URL}/v1/packages/${encodeURIComponent(fileName)}`;
  }
  return `/v1/packages/${encodeURIComponent(fileName)}`;
}

function publicCoreUrl(token) {
  if (PUBLIC_BASE_URL) {
    return `${PUBLIC_BASE_URL}/v1/core/${encodeURIComponent(token)}`;
  }
  return `/v1/core/${encodeURIComponent(token)}`;
}

function publicUsbProxyUrl(token) {
  if (PUBLIC_BASE_URL) {
    return `${PUBLIC_BASE_URL}/v1/usb-proxy/${encodeURIComponent(token)}`;
  }
  return `/v1/usb-proxy/${encodeURIComponent(token)}`;
}

function publicHailoUrl(token) {
  if (PUBLIC_BASE_URL) {
    return `${PUBLIC_BASE_URL}/v1/hailo/${encodeURIComponent(token)}`;
  }
  return `/v1/hailo/${encodeURIComponent(token)}`;
}

function sendIssuedPackage(res, kind, token, filePath, downloadName) {
  res.download(filePath, downloadName, (error) => {
    if (!error) {
      consumeIssuedPackage(kind, token);
      return;
    }
    if (!res.headersSent) {
      res.status(500).json({ ok: false, error: error.message || "download failed" });
    }
  });
}

function requestComponentVersion(req, name) {
  const components = req.body && typeof req.body.components === "object" ? req.body.components : {};
  const component = components && typeof components[name] === "object" ? components[name] : {};
  return String(component.version || "").trim();
}

function buildComponentUpdate({ currentVersion, targetVersion, buildPackage }) {
  const current = String(currentVersion || "").trim();
  const target = String(targetVersion || "").trim();
  const result = {
    update_available: false,
    current_version: current,
    latest_version: target
  };
  if (!target || current === target) {
    return result;
  }
  const componentPackage = buildPackage(target);
  result.latest_version = String(componentPackage.version || target);
  if (componentPackage.mode !== "download") {
    result.note = componentPackage.note || "component release is not configured on this server";
    return result;
  }
  result.update_available = true;
  result.package = componentPackage;
  return result;
}

function componentUpdateMissingRequiredPackage(component) {
  return Boolean(
    component &&
    component.latest_version &&
    component.current_version !== component.latest_version &&
    !component.update_available &&
    component.note
  );
}

function releaseComponentVersion(latest, field, fallback) {
  if (latest && latest.version) {
    return String(latest[field] || "").trim();
  }
  return fallback;
}

function revokedResponse(res, status, reason, details = {}) {
  return res.status(status).json({
    ok: false,
    error: reason,
    data: {
      revoked: true,
      reason,
      ...details
    }
  });
}

function licensePayloadFromRequest(req) {
  return req.body.license && typeof req.body.license === "object" ? req.body.license : {};
}

function invalidLicenseResponse(res, status, reason, details = {}) {
  return res.status(status).json({
    ok: false,
    error: reason,
    data: {
      reason,
      ...details
    }
  });
}

function deviceBlockCandidatesFromRequest(req, fallbackLicense = {}) {
  const deviceFingerprintHash = String(req.body.device_fingerprint_hash || req.body.device?.fingerprint_hash || fallbackLicense.device_fingerprint_hash || "").trim();
  const deviceId = String(req.body.device_id || req.body.device?.device_id || fallbackLicense.device_id || "").trim();
  const fingerprints = deviceFingerprintAliases(req.body.device);
  if (deviceFingerprintHash) fingerprints.add(deviceFingerprintHash);
  if (fallbackLicense.device_fingerprint_hash) fingerprints.add(String(fallbackLicense.device_fingerprint_hash).trim());
  return {
    device_id: deviceId,
    device_fingerprint_hash: deviceFingerprintHash,
    fingerprints: [...fingerprints].filter(Boolean)
  };
}

function deviceBlockedReason(record) {
  return record?.reason || "device is frozen";
}

function rejectBlockedDeviceIfNeeded(req, res, fallbackLicense = {}) {
  const candidates = deviceBlockCandidatesFromRequest(req, fallbackLicense);
  const blocked = findBlockedDevice(candidates);
  if (blocked) {
    revokedResponse(res, 403, deviceBlockedReason(blocked), {
      license_id: String(fallbackLicense.license_id || blocked.license_id || ""),
      device_id: candidates.device_id || blocked.device_id || "",
      device_fingerprint_hash: candidates.device_fingerprint_hash || blocked.device_fingerprint_hash || "",
      device_blocked: true
    });
    return true;
  }
  const activations = readActivations();
  const frozen = findFrozenActivationForDevice(activations, candidates);
  if (frozen) {
    revokedResponse(res, 403, frozen.activation.frozen_reason || "device is frozen", {
      license_id: String(fallbackLicense.license_id || frozen.activation.license_id || ""),
      device_id: candidates.device_id || frozen.activation.device_id || "",
      device_fingerprint_hash: candidates.device_fingerprint_hash || frozen.activation.device_fingerprint_hash || "",
      device_blocked: true
    });
    return true;
  }
  return false;
}

function validateActiveLicenseRequest(req, res, options = {}) {
  const deviceFingerprintHash = String(req.body.device_fingerprint_hash || req.body.device?.fingerprint_hash || "").trim();
  const license = licensePayloadFromRequest(req);
  if (rejectBlockedDeviceIfNeeded(req, res, license)) {
    return null;
  }
  if (!license.license_id || !deviceFingerprintHash || license.device_fingerprint_hash !== deviceFingerprintHash) {
    invalidLicenseResponse(res, 403, "valid license is required", {
      license_id: String(license.license_id || ""),
      device_id: String(license.device_id || ""),
      device_fingerprint_hash: deviceFingerprintHash
    });
    return null;
  }
  const activations = readActivations();
  const found = findActivationByLicense(activations, license, deviceFingerprintHash);
  if (!found) {
    invalidLicenseResponse(res, 403, "license is not active on this server", {
      license_id: license.license_id,
      device_id: String(license.device_id || ""),
      device_fingerprint_hash: deviceFingerprintHash
    });
    return null;
  }
  const keys = readKeys();
  const keyRecord = keys[found.keyHash] || {};
  const reason = revocationReason(keyRecord, found.activation);
  if (reason) {
    markActivationRevoked(found.keyHash);
    revokedResponse(res, 403, reason, {
      license_id: license.license_id,
      device_id: found.activation.device_id || license.device_id || "",
      device_fingerprint_hash: deviceFingerprintHash
    });
    return null;
  }
  return { activations, keys, found, keyRecord, license, deviceFingerprintHash };
}

function ensureModelKeyForCheckedRequest(req, checked) {
  if (!checked?.keys || !checked?.found) return checked?.keyRecord || {};
  const candidate = req.body?.model_key;
  if (!requestIncludesModelKey(req) || (candidate !== null && typeof candidate !== "object")) {
    return checked.keyRecord || {};
  }
  const modelKeyState = ensureKeyModelKey(checked.keys, checked.found.keyHash, req.body?.model_key);
  if (modelKeyState.changed) {
    writeKeys(checked.keys);
  }
  if (modelKeyState.record) {
    checked.keyRecord = modelKeyState.record;
  }
  return checked.keyRecord || checked.keys[checked.found.keyHash] || {};
}

function checkedUiBrand(checked) {
  return normalizeUiBrand(
    checked?.found?.activation?.ui_brand ||
    checked?.keyRecord?.ui_brand ||
    checked?.license?.ui_brand
  );
}

function isBeforeXcshMinVersion(version) {
  return compareVersionText(version, XCSH_MIN_UPDATE_VERSION) < 0;
}

function requestIncludesModelKey(req) {
  return Boolean(req?.body && Object.prototype.hasOwnProperty.call(req.body, "model_key"));
}

function clientIp(req) {
  return String(req.ip || req.socket?.remoteAddress || "unknown").trim() || "unknown";
}

function normalizeClientIp(value) {
  let text = String(value || "").trim();
  if (!text || text === "unknown") return "";
  if (text.includes(",")) text = text.split(",")[0].trim();
  if (text.startsWith("::ffff:")) text = text.slice("::ffff:".length);
  if (text === "::1") return "127.0.0.1";
  return text;
}

function ipv4Parts(ip) {
  if (net.isIP(ip) !== 4) return null;
  return ip.split(".").map((part) => Number(part));
}

function privateIpLocation(ip) {
  const parts = ipv4Parts(ip);
  if (parts) {
    const [a, b] = parts;
    if (a === 127) return "本机";
    if (a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168)) return "内网";
    if (a === 169 && b === 254) return "链路本地";
    if (a === 100 && b >= 64 && b <= 127) return "运营商内网";
    if (a === 0 || a >= 224 || (a === 192 && b === 0) || (a === 198 && (b === 18 || b === 19))) return "保留地址";
    return "";
  }
  const normalized = String(ip || "").toLowerCase();
  if (net.isIP(normalized) !== 6) return "未知";
  if (normalized === "::1") return "本机";
  if (normalized.startsWith("fc") || normalized.startsWith("fd")) return "内网";
  if (normalized.startsWith("fe80:")) return "链路本地";
  return "";
}

function cachedIpLocation(ip) {
  const cached = ipLocationCache.get(ip);
  if (!cached) return "";
  if (Date.now() - Number(cached.checkedAt || 0) > IP_LOCATION_CACHE_MS) {
    ipLocationCache.delete(ip);
    return "";
  }
  return cached.location || "";
}

function formatIpLocationPayload(payload) {
  const parts = [];
  for (const key of ["country", "regionName", "city"]) {
    const value = String(payload?.[key] || "").trim();
    if (value && !parts.includes(value)) parts.push(value);
  }
  const location = parts.join(" ");
  const isp = String(payload?.isp || "").trim();
  return [location, isp].filter(Boolean).join(" · ");
}

function canLookupPublicIpLocation() {
  return Boolean(
    IP_LOCATION_LOOKUP_ENABLED &&
    IP_LOCATION_ENDPOINT &&
    typeof fetch === "function" &&
    typeof AbortController === "function"
  );
}

async function lookupPublicIpLocation(ip) {
  if (!canLookupPublicIpLocation()) return "";
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), IP_LOCATION_LOOKUP_TIMEOUT_MS);
  try {
    const url = IP_LOCATION_ENDPOINT.replaceAll("{ip}", encodeURIComponent(ip));
    const response = await fetch(url, {
      headers: { "Accept": "application/json" },
      signal: controller.signal
    });
    if (!response.ok) return "";
    const payload = await response.json();
    if (payload?.status && payload.status !== "success") return "";
    return formatIpLocationPayload(payload);
  } catch {
    return "";
  } finally {
    clearTimeout(timer);
  }
}

function scheduleIpLocationLookup(keyHash, ip) {
  if (!keyHash || !ip || pendingIpLocationLookups.has(ip)) return;
  const promise = lookupPublicIpLocation(ip)
    .then((location) => {
      const finalLocation = location || "公网 IP";
      if (location) ipLocationCache.set(ip, { location, checkedAt: Date.now() });
      const activations = readActivations();
      let changed = false;
      const checkedAt = isoNow();
      for (const activation of Object.values(activations || {})) {
        if (!activation || normalizeClientIp(activation.ip || activation.last_ip) !== ip) continue;
        activation.ip_location = finalLocation;
        activation.ip_location_checked_at = checkedAt;
        changed = true;
      }
      if (changed) writeActivations(activations);
    })
    .catch(() => {})
    .finally(() => {
      pendingIpLocationLookups.delete(ip);
    });
  pendingIpLocationLookups.set(ip, promise);
}

function noteActivationClient(req, activations, keyHash, now = isoNow()) {
  const activation = activations[keyHash];
  if (!activation) return;
  const ip = normalizeClientIp(clientIp(req));
  if (!ip) return;
  const previousIp = normalizeClientIp(activation.ip || activation.last_ip);
  const privateLocation = privateIpLocation(ip);
  const cachedLocation = privateLocation || cachedIpLocation(ip);
  const fallbackLocation = canLookupPublicIpLocation() ? "查询中" : "公网 IP";
  activation.ip = ip;
  activation.last_ip = ip;
  activation.ip_location = cachedLocation || (previousIp === ip ? activation.ip_location : "") || fallbackLocation;
  activation.ip_seen_at = now;
  const binding = bindingEvidenceFromDevice(req.body?.device);
  if (binding) {
    activation.binding_hardware = binding;
  }
  if (!privateLocation && !cachedLocation && canLookupPublicIpLocation()) {
    scheduleIpLocationLookup(keyHash, ip);
  }
}

function cleanupRateBuckets(now) {
  if (now < nextRateCleanupAt) return;
  for (const [key, bucket] of activateRateBuckets.entries()) {
    if (!bucket || now >= bucket.resetAt) {
      activateRateBuckets.delete(key);
    }
  }
  nextRateCleanupAt = now + ACTIVATE_RATE_WINDOW_MS;
}

function hitRateLimit(scope, key, maxHits, now) {
  if (!key) return null;
  cleanupRateBuckets(now);
  const bucketKey = `${scope}:${key}`;
  let bucket = activateRateBuckets.get(bucketKey);
  if (!bucket || now >= bucket.resetAt) {
    bucket = { count: 0, resetAt: now + ACTIVATE_RATE_WINDOW_MS };
    activateRateBuckets.set(bucketKey, bucket);
  }
  bucket.count += 1;
  return {
    limited: bucket.count > maxHits,
    retryAfterSeconds: Math.max(1, Math.ceil((bucket.resetAt - now) / 1000))
  };
}

function activateRateLimit(req, res, next) {
  const now = Date.now();
  const licenseKey = String(req.body.license_key || "").trim();
  const deviceFingerprintHash = String(req.body.device_fingerprint_hash || req.body.device?.fingerprint_hash || "").trim();
  const checks = [
    hitRateLimit("ip", clientIp(req), ACTIVATE_RATE_MAX_IP, now),
    licenseKey ? hitRateLimit("license", sha256(licenseKey), ACTIVATE_RATE_MAX_LICENSE, now) : null,
    deviceFingerprintHash ? hitRateLimit("device", sha256(deviceFingerprintHash), ACTIVATE_RATE_MAX_DEVICE, now) : null
  ];
  const blocked = checks.find((check) => check?.limited);
  if (blocked) {
    res.setHeader("Retry-After", String(blocked.retryAfterSeconds));
    return res.status(429).json({
      ok: false,
      error: "too many activation attempts; please retry later",
      data: { retry_after_seconds: blocked.retryAfterSeconds }
    });
  }
  next();
}

function deviceFingerprintAliases(device) {
  const aliases = new Set();
  if (!device || typeof device !== "object") return aliases;
  for (const key of ["fingerprint_hash", "previous_fingerprint_hash", "legacy_fingerprint_hash", "device_fingerprint_hash"]) {
    const value = String(device[key] || "").trim();
    if (value) aliases.add(value);
  }
  if (Array.isArray(device.fingerprint_aliases)) {
    for (const item of device.fingerprint_aliases) {
      const value = String(item || "").trim();
      if (value) aliases.add(value);
    }
  }
  if (device.recovery && typeof device.recovery === "object") {
    for (const key of ["fingerprint_hash", "previous_fingerprint_hash", "legacy_fingerprint_hash", "device_fingerprint_hash"]) {
      const value = String(device.recovery[key] || "").trim();
      if (value) aliases.add(value);
    }
    if (Array.isArray(device.recovery.fingerprints)) {
      for (const item of device.recovery.fingerprints) {
        const value = String(item || "").trim();
        if (value) aliases.add(value);
      }
    }
  }
  return aliases;
}

function activationForLicenseId(activations, licenseId) {
  const id = String(licenseId || "").trim();
  if (!id) return null;
  for (const [keyHash, activation] of Object.entries(activations || {})) {
    if (activation?.license_id === id) {
      return { keyHash, activation };
    }
  }
  return null;
}

function bindingMacs(binding) {
  const values = new Set();
  if (!binding || typeof binding !== "object") return values;
  if (Array.isArray(binding.board_mac_addresses)) {
    for (const item of binding.board_mac_addresses) {
      const value = String(item || "").trim().toLowerCase();
      if (value && value !== "00:00:00:00:00:00") values.add(value);
    }
  }
  if (Array.isArray(binding.board_macs)) {
    for (const item of binding.board_macs) {
      const value = String(item?.address || "").trim().toLowerCase();
      if (value && value !== "00:00:00:00:00:00") values.add(value);
    }
  }
  return values;
}

function stableCpuFields(binding) {
  const cpu = binding && typeof binding === "object" && binding.cpu && typeof binding.cpu === "object"
    ? binding.cpu
    : {};
  const result = {};
  for (const key of ["Serial", "cpu_serial"]) {
    const value = String(cpu[key] || "").trim();
    if (value) result[key] = value;
  }
  return result;
}

function samePhysicalBoardBinding(previousBinding, currentBinding) {
  if (!previousBinding || typeof previousBinding !== "object" || !currentBinding || typeof currentBinding !== "object") {
    return false;
  }
  const previousMacs = bindingMacs(previousBinding);
  const currentMacs = bindingMacs(currentBinding);
  if (!previousMacs.size || !currentMacs.size) return false;
  let sharedMac = false;
  for (const value of previousMacs) {
    if (currentMacs.has(value)) {
      sharedMac = true;
      break;
    }
  }
  if (!sharedMac) return false;
  const previousCpu = stableCpuFields(previousBinding);
  const currentCpu = stableCpuFields(currentBinding);
  if (Object.keys(previousCpu).length && Object.keys(currentCpu).length && stableJson(previousCpu) !== stableJson(currentCpu)) {
    return false;
  }
  return true;
}

function bindingEvidenceFromDevice(device) {
  if (!device || typeof device !== "object") return null;
  if (device.binding_hardware && typeof device.binding_hardware === "object") {
    return device.binding_hardware;
  }
  if (device.binding_hardware_current && typeof device.binding_hardware_current === "object") {
    return device.binding_hardware_current;
  }
  return null;
}

function bindingEvidenceFromActivation(activation) {
  if (!activation || typeof activation !== "object") return null;
  if (activation.binding_hardware && typeof activation.binding_hardware === "object") {
    return activation.binding_hardware;
  }
  if (activation.binding_hardware_current && typeof activation.binding_hardware_current === "object") {
    return activation.binding_hardware_current;
  }
  return null;
}

function validCurrentBindingForFingerprint(device, deviceFingerprintHash) {
  const binding = device && typeof device === "object" && device.binding_hardware && typeof device.binding_hardware === "object"
    ? device.binding_hardware
    : null;
  if (!binding) return null;
  const calculated = sha256(stableJson(binding));
  return calculated === deviceFingerprintHash ? binding : null;
}

function repairBoardEvidenceOk(device, deviceFingerprintHash) {
  if (!device || typeof device !== "object") return false;
  const currentBinding = validCurrentBindingForFingerprint(device, deviceFingerprintHash);
  if (!currentBinding) return false;
  const recovery = device.recovery && typeof device.recovery === "object" ? device.recovery : {};
  const candidates = [];
  for (const key of ["binding_hardware", "binding_hardware_current"]) {
    if (recovery[key] && typeof recovery[key] === "object") candidates.push(recovery[key]);
  }
  return candidates.some((candidate) => samePhysicalBoardBinding(candidate, currentBinding));
}

function activationBoardEvidenceOk(activation, device, deviceFingerprintHash) {
  if (!activation || !device || typeof device !== "object") return false;
  const currentBinding = validCurrentBindingForFingerprint(device, deviceFingerprintHash);
  if (!currentBinding) return false;
  const storedBinding = bindingEvidenceFromActivation(activation);
  return Boolean(storedBinding && samePhysicalBoardBinding(storedBinding, currentBinding));
}

router.get("/health", (_req, res) => {
  res.json({ ok: true, data: { service: "aiassistance-license-server", port: PORT } });
});

router.get("/v1/announcement", (req, res) => {
  const announcement = readAnnouncement(req.query.ui_brand || req.query.brand);
  res.json({
    ok: true,
    data: {
      enabled: Boolean(announcement.enabled),
      title: String(announcement.title || ""),
      content: String(announcement.content || ""),
      version: String(announcement.version || ""),
      updated_at: String(announcement.updated_at || "")
    }
  });
});

router.post("/v1/activate", activateRateLimit, (req, res) => {
  try {
    requirePrivateKey();
    const licenseKey = String(req.body.license_key || "").trim();
    const deviceFingerprintHash = String(req.body.device_fingerprint_hash || req.body.device?.fingerprint_hash || "").trim();
    const deviceId = String(req.body.device_id || req.body.device?.device_id || "").trim();
    if (!licenseKey || !deviceFingerprintHash || !deviceId) {
      return res.status(400).json({ ok: false, error: "license_key, device_id and device_fingerprint_hash are required" });
    }
    if (rejectBlockedDeviceIfNeeded(req, res, {
      device_id: deviceId,
      device_fingerprint_hash: deviceFingerprintHash
    })) {
      return;
    }

    const keys = readKeys();
    const activations = readActivations();
    const keyHash = sha256(licenseKey);
    let record = keys[keyHash];
    if (!record) {
      return res.status(403).json({ ok: false, error: "invalid license key" });
    }
    const existing = activations[keyHash];
    const reason = revocationReason(record, existing);
    if (reason) {
      if (!existing) {
        return res.status(403).json({ ok: false, error: reason });
      }
      markActivationRevoked(keyHash);
      return revokedResponse(res, 403, reason, {
        license_id: existing.license_id || "",
        device_id: existing.device_id || deviceId,
        device_fingerprint_hash: existing.device_fingerprint_hash || deviceFingerprintHash
      });
    }
    if (existing && existing.device_fingerprint_hash !== deviceFingerprintHash) {
      const aliases = deviceFingerprintAliases(req.body.device);
      const matchedByAlias = aliases.has(existing.device_fingerprint_hash);
      const matchedByServerEvidence = activationBoardEvidenceOk(existing, req.body.device, deviceFingerprintHash);
      if (!matchedByAlias && !matchedByServerEvidence) {
        return res.status(409).json({ ok: false, error: "license key is already bound to another device" });
      }
      if (matchedByAlias && !repairBoardEvidenceOk(req.body.device, deviceFingerprintHash)) {
        return res.status(409).json({ ok: false, error: "current hardware evidence does not match previous device" });
      }
    }
    if (requestIncludesModelKey(req)) {
      const modelKeyState = ensureKeyModelKey(keys, keyHash, req.body.model_key);
      record = modelKeyState.record || record;
      if (modelKeyState.changed) {
        writeKeys(keys);
      }
    }

    const now = isoNow();
    const plan = normalizePlan(record.plan);
    const activatedAt = existing?.activated_at || existing?.issued_at || now;
    const expiresAt = plan === "trial"
      ? (existing?.expires_at || addSecondsIso(activatedAt, normalizeTrialDurationSeconds(record.trial_duration_seconds)))
      : "";
    const activationDraft = {
      ...existing,
      plan,
      activated_at: activatedAt,
      expires_at: expiresAt
    };
    const draftReason = revocationReason(record, activationDraft);
    if (draftReason) {
      if (existing) {
        markActivationRevoked(keyHash);
        return revokedResponse(res, 403, draftReason, {
          license_id: existing.license_id || "",
          device_id: existing.device_id || deviceId,
          device_fingerprint_hash: existing.device_fingerprint_hash || deviceFingerprintHash
        });
      }
      return res.status(403).json({ ok: false, error: draftReason });
    }
    const license = buildSignedLicense({
      existing,
      activation: activationDraft,
      keyRecord: record,
      deviceId,
      deviceFingerprintHash,
      now
    });

    activations[keyHash] = {
      ...existing,
      license_id: license.license_id,
      device_id: deviceId,
      device_fingerprint_hash: deviceFingerprintHash,
      binding_hardware: bindingEvidenceFromDevice(req.body.device) || existing?.binding_hardware || {},
      plan,
      ui_brand: normalizeUiBrand(record.ui_brand),
      activated_at: activatedAt,
      expires_at: plan === "trial" ? license.expires_at : "",
      issued_at: license.issued_at,
      last_seen_at: now
    };
    noteActivationClient(req, activations, keyHash, now);
    if (plan === "trial") {
      activations[keyHash].last_heartbeat_at = now;
    }
    writeActivations(activations);

    res.json({
      ok: true,
      data: {
        license,
        online_grant: plan === "trial" ? buildOnlineGrant({ license, activation: activations[keyHash], now }) : null,
        core: buildCorePackage(license, publicCoreUrl),
        usb_proxy: buildUsbProxyPackage(license, publicUsbProxyUrl),
        model_key: modelKeyPackage(record),
        version: req.body.version || "",
        update: { update_available: false }
      }
    });
  } catch (error) {
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
});

router.post("/v1/repair", activateRateLimit, (req, res) => {
  try {
    requirePrivateKey();
    const deviceFingerprintHash = String(req.body.device_fingerprint_hash || req.body.device?.fingerprint_hash || "").trim();
    const deviceId = String(req.body.device_id || req.body.device?.device_id || "").trim();
    const previousLicense = licensePayloadFromRequest(req);
    const previousFingerprintHash = String(previousLicense.device_fingerprint_hash || "").trim();
    if (!previousLicense.license_id || !previousFingerprintHash || !deviceFingerprintHash || !deviceId) {
      return res.status(400).json({ ok: false, error: "license, device_id and device_fingerprint_hash are required" });
    }
    if (rejectBlockedDeviceIfNeeded(req, res, previousLicense)) {
      return;
    }
    const aliases = deviceFingerprintAliases(req.body.device);
    if (!aliases.has(previousFingerprintHash)) {
      return res.status(409).json({ ok: false, error: "current device does not contain previous license fingerprint evidence" });
    }
    if (!repairBoardEvidenceOk(req.body.device, deviceFingerprintHash)) {
      return res.status(409).json({ ok: false, error: "current hardware evidence does not match previous device" });
    }
    const activations = readActivations();
    const found = activationForLicenseId(activations, previousLicense.license_id);
    if (!found || found.activation.device_fingerprint_hash !== previousFingerprintHash) {
      return revokedResponse(res, 403, "license is not active on this server", {
        license_id: previousLicense.license_id,
        device_id: String(previousLicense.device_id || deviceId || ""),
        device_fingerprint_hash: deviceFingerprintHash
      });
    }
    const keys = readKeys();
    const modelKeyState = requestIncludesModelKey(req)
      ? ensureKeyModelKey(keys, found.keyHash, req.body.model_key)
      : { record: null, changed: false };
    const keyRecord = modelKeyState.record || keys[found.keyHash] || {};
    if (modelKeyState.changed) {
      writeKeys(keys);
    }
    const reason = revocationReason(keyRecord, found.activation);
    if (reason) {
      markActivationRevoked(found.keyHash);
      return revokedResponse(res, 403, reason, {
        license_id: previousLicense.license_id,
        device_id: found.activation.device_id || previousLicense.device_id || "",
        device_fingerprint_hash: previousFingerprintHash
      });
    }
    const now = isoNow();
    const migratedFingerprints = Array.isArray(found.activation.migrated_fingerprints)
      ? found.activation.migrated_fingerprints.filter((value) => typeof value === "string" && value)
      : [];
    if (!migratedFingerprints.includes(previousFingerprintHash)) {
      migratedFingerprints.push(previousFingerprintHash);
    }
    found.activation.device_id = deviceId;
    found.activation.device_fingerprint_hash = deviceFingerprintHash;
    found.activation.binding_hardware = bindingEvidenceFromDevice(req.body.device) || found.activation.binding_hardware || {};
    found.activation.last_repaired_at = now;
    found.activation.last_seen_at = now;
    found.activation.migrated_fingerprints = migratedFingerprints.slice(-16);
    const license = buildSignedLicense({
      existing: found.activation,
      activation: found.activation,
      keyRecord,
      deviceId,
      deviceFingerprintHash,
      now
    });
    found.activation.issued_at = license.issued_at;
    noteActivationClient(req, activations, found.keyHash, now);
    if (normalizePlan(found.activation.plan || keyRecord.plan) === "trial") {
      found.activation.last_heartbeat_at = now;
    }
    writeActivations(activations);
    res.json({
      ok: true,
      data: {
        license,
        online_grant: normalizePlan(found.activation.plan || keyRecord.plan) === "trial"
          ? buildOnlineGrant({ license, activation: found.activation, now })
          : null,
        core: buildCorePackage(license, publicCoreUrl),
        usb_proxy: buildUsbProxyPackage(license, publicUsbProxyUrl),
        model_key: modelKeyPackage(keyRecord),
        version: req.body.version || "",
        repair: {
          repaired: true,
          previous_device_fingerprint_hash: previousFingerprintHash,
          device_fingerprint_hash: deviceFingerprintHash
        }
      }
    });
  } catch (error) {
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
});

router.post("/v1/check-update", (req, res) => {
  const currentVersion = String(req.body.version || "");
  const preferFullPackage = req.body && (req.body.prefer_full === true || req.body.prefer_full_package === true);
  const targetVersion = String(req.body.target_version || req.body.requested_version || "").trim();
  const checked = validateActiveLicenseRequest(req, res);
  if (!checked) return;
  const { activations, found, license } = checked;
  ensureModelKeyForCheckedRequest(req, checked);
  if (targetVersion && checkedUiBrand(checked) === "xcsh" && isBeforeXcshMinVersion(targetVersion)) {
    return res.status(400).json({ ok: false, error: `XCSH 系统不能切换到 ${XCSH_MIN_UPDATE_VERSION} 之前的版本` });
  }

  found.activation.last_check_at = isoNow();
  noteActivationClient(req, activations, found.keyHash, found.activation.last_check_at);
  writeActivations(activations);

  const latest = targetVersion
    ? loadPackageByVersion(publicPackageUrl, targetVersion)
    : loadLatestPackage(publicPackageUrl);
  if (targetVersion && !latest) {
    return res.status(404).json({ ok: false, error: "requested update version not found" });
  }
  const selected = latest || {};
  let appPackage = selected.url && selected.version && selected.version !== currentVersion ? selected : null;
  const core = buildComponentUpdate({
    currentVersion: requestComponentVersion(req, "core"),
    targetVersion: releaseComponentVersion(selected, "core_version", CORE_VERSION),
    buildPackage: (version) => buildCorePackage(license, publicCoreUrl, version),
  });
  const usbProxy = buildComponentUpdate({
    currentVersion: requestComponentVersion(req, "usb_proxy"),
    targetVersion: releaseComponentVersion(selected, "usb_proxy_version", USB_PROXY_VERSION),
    buildPackage: (version) => buildUsbProxyPackage(license, publicUsbProxyUrl, version),
  });
  if (targetVersion && (componentUpdateMissingRequiredPackage(core) || componentUpdateMissingRequiredPackage(usbProxy))) {
    return res.status(409).json({ ok: false, error: "requested update version is missing required component releases" });
  }
  if (!appPackage && preferFullPackage && selected.url && selected.version && (core.update_available || usbProxy.update_available)) {
    appPackage = selected;
  }
  const updateAvailable = Boolean(appPackage || core.update_available || usbProxy.update_available);
  const grantData = found.activation.plan === "trial"
    ? { online_grant: buildOnlineGrant({ license, activation: found.activation, now: found.activation.last_check_at }) }
    : {};
  res.json({
    ok: true,
    data: {
      update_available: updateAvailable,
      latest_version: selected.version || currentVersion,
      target_version: targetVersion || "",
      release_notes: loadJson("latest-release-notes.json", { text: "" }).text || "",
      package: appPackage || null,
      components: {
        core,
        usb_proxy: usbProxy
      },
      ...grantData
    }
  });
});

router.post("/v1/update-versions", (req, res) => {
  const checked = validateActiveLicenseRequest(req, res);
  if (!checked) return;
  const { activations, found, license } = checked;
  ensureModelKeyForCheckedRequest(req, checked);

  found.activation.last_check_at = isoNow();
  noteActivationClient(req, activations, found.keyHash, found.activation.last_check_at);
  writeActivations(activations);

  const latest = loadLatestPackage(publicPackageUrl) || {};
  const versions = listUpdatePackages(publicPackageUrl);
  const visibleVersions = checkedUiBrand(checked) === "xcsh"
    ? versions.filter((item) => !isBeforeXcshMinVersion(item.version))
    : versions;
  const grantData = found.activation.plan === "trial"
    ? { online_grant: buildOnlineGrant({ license, activation: found.activation, now: found.activation.last_check_at }) }
    : {};
  res.json({
    ok: true,
    data: {
      latest_version: latest.version || "",
      versions: visibleVersions,
      release_notes: loadJson("latest-release-notes.json", { text: "" }).text || "",
      ...grantData
    }
  });
});

router.post("/v1/hailo/package", (req, res) => {
  try {
    const checked = validateActiveLicenseRequest(req, res);
    if (!checked) return;
    const { activations, found, license } = checked;
    ensureModelKeyForCheckedRequest(req, checked);

    found.activation.last_check_at = isoNow();
    noteActivationClient(req, activations, found.keyHash, found.activation.last_check_at);
    writeActivations(activations);

    const requestedVersion = String(req.body.hailo_version || req.body.version || HAILO_VERSION).trim();
    const requestedKernel = String(req.body.kernel_release || HAILO_KERNEL_RELEASE).trim();
    const requestedBoard = String(req.body.board_id || req.body.hailo_board_id || "").trim();
    const hailoPackage = buildHailoPackage(license, publicHailoUrl, requestedVersion, requestedKernel, requestedBoard);
    if (hailoPackage.mode !== "download") {
      return res.status(404).json({ ok: false, error: hailoPackage.note || "Hailo dependency package is not available" });
    }
    res.json({
      ok: true,
      data: {
        hailo: hailoPackage
      }
    });
  } catch (error) {
    res.status(500).json({ ok: false, error: error.message || String(error) });
  }
});

router.post("/v1/license-check", (req, res) => {
  const checked = validateActiveLicenseRequest(req, res, { revokedOnMissing: true });
  if (!checked) return;
  const keyRecord = ensureModelKeyForCheckedRequest(req, checked);
  const { activations, found, license } = checked;
  const plan = normalizePlan(found.activation.plan || keyRecord.plan || license.plan);
  const now = isoNow();
  if (plan !== "trial") {
    found.activation.last_heartbeat_at = now;
    found.activation.last_check_at = now;
    noteActivationClient(req, activations, found.keyHash, now);
    writeActivations(activations);
    return res.json({
      ok: true,
      data: {
        plan,
        server_time: now,
        online_required: false,
        model_key: modelKeyPackage(keyRecord)
      }
    });
  }
  const signedLicense = buildSignedLicense({
    existing: found.activation,
    activation: found.activation,
    keyRecord,
    deviceId: found.activation.device_id || license.device_id || "",
    deviceFingerprintHash: found.activation.device_fingerprint_hash || license.device_fingerprint_hash || "",
    now
  });
  found.activation.last_heartbeat_at = now;
  found.activation.last_check_at = now;
  noteActivationClient(req, activations, found.keyHash, now);
  writeActivations(activations);
  res.json({
    ok: true,
    data: {
      plan,
      server_time: now,
      license: signedLicense,
      online_grant: buildOnlineGrant({ license: signedLicense, activation: found.activation, now }),
      model_key: modelKeyPackage(keyRecord)
    }
  });
});

router.get("/v1/packages/:file", (req, res) => {
  const fileName = path.basename(req.params.file);
  const filePath = path.join(PACKAGE_DIR, fileName);
  if (!fs.existsSync(filePath)) {
    return res.status(404).json({ ok: false, error: "package not found" });
  }
  res.download(filePath, fileName);
});

router.get("/v1/core/:token", (req, res) => {
  const token = String(req.params.token || "").replace(/[^a-f0-9]/gi, "");
  const issued = resolveIssuedPackage("core", token);
  if (!issued) {
    return res.status(404).json({ ok: false, error: "core package not found or expired" });
  }
  sendIssuedPackage(res, "core", token, issued.filePath, "libai_core.so.enc");
});

router.get("/v1/usb-proxy/:token", (req, res) => {
  const token = String(req.params.token || "").replace(/[^a-f0-9]/gi, "");
  const issued = resolveIssuedPackage("usb_proxy", token);
  if (!issued) {
    return res.status(404).json({ ok: false, error: "usb-proxy package not found or expired" });
  }
  sendIssuedPackage(res, "usb_proxy", token, issued.filePath, "usb-proxy.enc");
});

router.get("/v1/hailo/:token", (req, res) => {
  const token = String(req.params.token || "").replace(/[^a-f0-9]/gi, "");
  const issued = resolveIssuedPackage("hailo", token);
  if (!issued) {
    return res.status(404).json({ ok: false, error: "Hailo package not found or expired" });
  }
  const record = issued.record || {};
  const fileName = issued.fileName || path.basename(issued.filePath);
  const safePart = (value, fallback) => String(value || fallback || "")
    .trim()
    .replace(/[^A-Za-z0-9._-]+/g, "-")
    .replace(/^-+|-+$/g, "") || fallback;
  const boardId = safePart(record.board_id, "orangepi");
  const kernelRelease = safePart(record.kernel_release, "kernel");
  const version = safePart(record.version, "runtime");
  const downloadName = `hailo-${boardId}-${kernelRelease}-${version}${fileName.endsWith(".tar.gz") ? ".tar.gz" : ".tar.zst"}`;
  sendIssuedPackage(res, "hailo", token, issued.filePath, downloadName);
});

app.use("/", router);
app.use("/aiassistance-api", router);

app.listen(PORT, () => {
  console.log(`aiAssistance license server listening on ${PORT}`);
});
