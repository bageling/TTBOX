#include "auth/TtboxLicenseClient.hpp"
#include "auth/LicenseConstants.hpp"   // B-CONST-4：心跳 60/180 单点真源

#include "auth/TtboxCanonical.hpp"   // T1.07：canonical 纯函数（末尾无换行，M2.07 修复）
#include "common/Json.hpp"
#include "common/Logger.hpp"

#include <chrono>
#include <cstdint>
#include <cstring>
#include <sstream>
#include <string>
#include <random>
#include <vector>

#if defined(_WIN32)
#pragma comment(lib, "ws2_32.lib")
#pragma comment(lib, "crypt32.lib")
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>
#include <wincrypt.h>
#else
#include <arpa/inet.h>
#include <errno.h>
#include <netdb.h>
#include <netinet/in.h>
#if defined(__linux__) || defined(__APPLE__)
#include <openssl/err.h>
#include <openssl/hmac.h>
#include <openssl/ssl.h>
#endif
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>
#endif

#include "common/Json.hpp"

namespace ttbox::core::auth {

namespace {

int64_t now_unix_ms() {
    return static_cast<int64_t>(
        std::chrono::duration_cast<std::chrono::milliseconds>(
            std::chrono::system_clock::now().time_since_epoch())
            .count());
}

std::string unix_timestamp_sec() {
    return std::to_string(
        static_cast<int64_t>(
            std::chrono::duration_cast<std::chrono::seconds>(
                std::chrono::system_clock::now().time_since_epoch())
                .count()));
}

std::string generate_nonce(int len = 16) {
    static const char chars[] =
        "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ";
    // ★ 2026-09-23：原来用 system_clock 播种 mt19937 ⇒ nonce **可预测**
    //   （攻击者只要知道大致时间就能枚举），HMAC 签名里的 nonce 等于没起作用。
    //   改用 random_device（Linux 下读 /dev/urandom）。
    //   注：本文件当前被 AUTH 门控、线上只装 OfflineCardClient，但 T2.03 一旦
    //   打开在线接入就会走到这里 ⇒ 现在改比那时改便宜。
    std::random_device rd;
    std::mt19937 gen(rd());
    std::uniform_int_distribution<> dist(0, sizeof(chars) - 2);
    std::string s;
    s.reserve(len);
    for (int i = 0; i < len; ++i) s += chars[dist(gen)];
    return s;
}

// ---- M2：签名卡内容解析（features / plan / uiBrand）----
//
// 字段命名沿用本协议既有的 camelCase 约定（clientToken / expireAt / heartbeatInterval…），
// 故品牌字段名为 `uiBrand`，而出参结构体字段为 `ui_brand`。
//
// ★ 服务端契约要求：card-login 与 heartbeat 的**成功响应都必须携带这三个字段**
//   （features 可为空数组，表示明确不授予任何功能位）。原因：
//   本函数只负责「把服务端说什么写进 out」，不持有历史；若服务端在成功响应里省略字段，
//   调用方将无法区分「服务端本轮没下发」与「服务端明确撤销」——
//   前者会误判成后者，表现为功能位每次心跳后静默清空（难排查的生产事故）。
//   失败路径的「回落历史值」由 apply_check_result 负责（见 LicenseStateMachine.hpp），
//   本函数**不**做任何回落或过滤：归一化与闭集过滤唯一发生在 to_snapshot。
//
// 缺失字段的处理：不写入 ⇒ out 对应项保持默认空 ⇒ 由状态机按失败/成功路径分别裁决。
void parse_card_content(const JsonValue& root, LicenseStatus& out) {
    if (const JsonValue* f = root.find("features"); f != nullptr && f->is_array()) {
        std::vector<std::string> feats;
        feats.reserve(f->as_array().size());
        for (const JsonValue& v : f->as_array()) {
            if (v.is_string()) feats.push_back(v.as_string());
        }
        out.features = std::move(feats);
    }
    if (const JsonValue* p = root.find("plan"); p != nullptr && p->is_string()) {
        out.plan = p->as_string();
    }
    if (const JsonValue* b = root.find("uiBrand"); b != nullptr && b->is_string()) {
        out.ui_brand = b->as_string();
    }
}

// Parse host:port from URL (http://host:port or http://host)
std::pair<std::string, int> parse_url_host(const std::string& url) {
    std::string host = url;
    int port = 80;
    // strip scheme
    size_t scheme_end = host.find("://");
    if (scheme_end != std::string::npos) host = host.substr(scheme_end + 3);
    // strip path
    size_t path_start = host.find('/');
    if (path_start != std::string::npos) host = host.substr(0, path_start);
    // extract port
    size_t colon = host.find(':');
    if (colon != std::string::npos) {
        port = std::stoi(host.substr(colon + 1));
        host = host.substr(0, colon);
    }
    return {host, port};
}

// Simple HTTP POST over TCP (no TLS) — used when server is HTTP
// Returns empty on failure; body on success
std::string http_post_plain(const std::string& host, int port,
                             const std::string& path,
                             const std::string& body_json,
                             int* out_status) {
#if defined(_WIN32)
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return {};
    int fd = socket(AF_INET, SOCK_STREAM, 0);
    if (fd < 0) { WSACleanup(); return {}; }
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<u_short>(port));
    addr.sin_addr.s_addr = inet_addr(host.c_str());
    if (connect(fd, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) < 0) {
        closesocket(fd); WSACleanup(); return {};
    }
    std::ostringstream req;
    req << "POST " << path << " HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "Content-Length: " << body_json.size() << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    req << body_json;
    std::string reqs = req.str();
    send(fd, reqs.c_str(), static_cast<int>(reqs.size()), 0);
    std::string total;
    char buf[4096];
    int n;
    while ((n = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, n);
    closesocket(fd);
    WSACleanup();
#elif defined(__linux__) || defined(__APPLE__)
    struct addrinfo hints, *res = nullptr;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    std::snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host.c_str(), port_str, &hints, &res) != 0) return {};
    int fd = -1;
    for (struct addrinfo* p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        struct timeval tv;
        tv.tv_sec = 10; tv.tv_usec = 0;
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return {};
    std::ostringstream req;
    req << "POST " << path << " HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "Content-Length: " << body_json.size() << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    req << body_json;
    std::string reqs = req.str();
    ssize_t sent = 0;
    while (sent < static_cast<ssize_t>(reqs.size())) {
        ssize_t w = send(fd, reqs.data() + sent, reqs.size() - sent, 0);
        if (w <= 0) { close(fd); return {}; }
        sent += w;
    }
    std::string total;
    char buf[4096];
    ssize_t r;
    while ((r = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, static_cast<size_t>(r));
    close(fd);
#else
    (void)host; (void)port; (void)path; (void)body_json; (void)out_status;
    return {};
#endif
    // Parse status line
    size_t line1 = total.find("\r\n");
    if (line1 == std::string::npos) return {};
    std::string l1 = total.substr(0, line1);
    auto sp1 = l1.find(' ');
    if (sp1 == std::string::npos) return {};
    int status = std::atoi(l1.substr(sp1 + 1).c_str());
    if (out_status) *out_status = status;
    if (status != 200) return {};
    size_t hdr_end = total.find("\r\n\r\n");
    if (hdr_end == std::string::npos) return {};
    return total.substr(hdr_end + 4);
}

}  // namespace

// ---- HMAC-SHA256 (platform-dependent) ----

std::string TtboxLicenseClient::sign_request(const std::string& method,
                                              const std::string& path,
                                              const std::string& timestamp,
                                              const std::string& nonce,
                                              const std::string& body) {
    // ★ 契约 §2（已按云端 client_sign.go 的 clientSigningString 实测校准，M2.07）：
    //   canonical 末尾**无换行**；多拼换行 ⇒ 云端 HMAC 校验失败 401。
    const std::string canonical =
        build_canonical(method, path, timestamp, nonce, body);

    // ★ client_secret 为空 ⇒ 拒绝签名并报错（禁止"空 secret 照签"，见 §3.3）。
    if (client_secret_.empty()) {
        TTBOX_LOG_ERROR("client_secret 未配置，拒绝签名");
        return {};
    }
#if defined(__linux__) || defined(__APPLE__)
    unsigned char result[EVP_MAX_MD_SIZE];
    unsigned int result_len = 0;
    HMAC(EVP_sha256(), client_secret_.data(),
         static_cast<int>(client_secret_.size()),
         reinterpret_cast<const unsigned char*>(canonical.data()),
         canonical.size(), result, &result_len);
    static const char hex[] = "0123456789abcdef";
    std::string out;
    out.reserve(result_len * 2);
    for (unsigned int i = 0; i < result_len; ++i) {
        out += hex[(result[i] >> 4) & 0x0F];
        out += hex[result[i] & 0x0F];
    }
    return out;
#elif defined(_WIN32)
    // Windows: use BCrypt for HMAC-SHA256
    BCryptAlgorithmHandle hAlg = nullptr;
    NTSTATUS nt = BCryptOpenAlgorithmProvider(&hAlg, BCRYPT_SHA256_ALGORITHM,
                                               NULL, 0);
    if (nt != STATUS_SUCCESS || !hAlg) return {};
    BCryptHashHandle hHash = nullptr;
    nt = BCryptCreateHash(hAlg, &hHash, NULL, 0,
                          reinterpret_cast<UCHAR*>(const_cast<char*>(client_secret_.data())),
                          static_cast<ULONG>(client_secret_.size()), 0);
    if (nt != STATUS_SUCCESS || !hHash) {
        BCryptCloseAlgorithmProvider(hAlg, 0);
        return {};
    }
    nt = BCryptHashData(hHash,
                        reinterpret_cast<UCHAR*>(const_cast<char*>(canonical.data())),
                        static_cast<ULONG>(canonical.size()), 0);
    if (nt != STATUS_SUCCESS) {
        BCryptDestroyHash(hHash);
        BCryptCloseAlgorithmProvider(hAlg, 0);
        return {};
    }
    ULONG dwHashLen = 0;
    BCryptFinishHash(hHash, NULL, 0, &dwHashLen, 0);
    std::vector<UCHAR> hashBuf(dwHashLen);
    nt = BCryptFinishHash(hHash, hashBuf.data(), dwHashLen, &dwHashLen, 0);
    BCryptDestroyHash(hHash);
    BCryptCloseAlgorithmProvider(hAlg, 0);
    if (nt != STATUS_SUCCESS) return {};
    static const char hex[] = "0123456789abcdef";
    std::string out;
    out.reserve(dwHashLen * 2);
    for (ULONG i = 0; i < dwHashLen; ++i) {
        out += hex[(hashBuf[i] >> 4) & 0x0F];
        out += hex[hashBuf[i] & 0x0F];
    }
    return out;
#else
    (void)method; (void)path; (void)timestamp; (void)nonce; (void)body;
    return {};
#endif
}

std::string TtboxLicenseClient::api_get(const std::string& path) {
#if !defined(__linux__) && !defined(__APPLE__) && !defined(_WIN32)
    (void)path;
    return {};
#endif
    auto [host, port] = parse_url_host(server_url_);
    std::string ts = unix_timestamp_sec();
    std::string nonce = generate_nonce();
    std::string sig = sign_request("GET", path, ts, nonce, "");
    if (sig.empty()) return {};   // client_secret 未配置 ⇒ 拒签，不发请求（§3.3）

#if defined(_WIN32)
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return {};
    // Resolve hostname
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    std::snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host.c_str(), port_str, &hints, &res) != 0) {
        WSACleanup(); return {};
    }
    SOCKET fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd == INVALID_SOCKET) { freeaddrinfo(res); WSACleanup(); return {}; }
    if (connect(fd, res->ai_addr, static_cast<int>(res->ai_addrlen)) != 0) {
        closesocket(fd); freeaddrinfo(res); WSACleanup(); return {};
    }
    freeaddrinfo(res);
    std::ostringstream req;
    req << "GET " << path << " HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "X-App-Key: " << app_key_ << "\r\n";
    req << "X-Timestamp: " << ts << "\r\n";
    req << "X-Nonce: " << nonce << "\r\n";
    req << "X-Signature: " << sig << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    std::string reqs = req.str();
    send(fd, reqs.c_str(), static_cast<int>(reqs.size()), 0);
    std::string total;
    char buf[4096];
    int n;
    while ((n = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, n);
    closesocket(fd);
    WSACleanup();
#elif defined(__linux__) || defined(__APPLE__)
    struct addrinfo hints, *res = nullptr;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    std::snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host.c_str(), port_str, &hints, &res) != 0) return {};
    int fd = -1;
    for (struct addrinfo* p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        struct timeval tv;
        tv.tv_sec = 10; tv.tv_usec = 0;
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return {};
    std::ostringstream req;
    req << "GET " << path << " HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "X-App-Key: " << app_key_ << "\r\n";
    req << "X-Timestamp: " << ts << "\r\n";
    req << "X-Nonce: " << nonce << "\r\n";
    req << "X-Signature: " << sig << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    std::string reqs = req.str();
    ssize_t sent = 0;
    while (sent < static_cast<ssize_t>(reqs.size())) {
        ssize_t w = send(fd, reqs.data() + sent, reqs.size() - sent, 0);
        if (w <= 0) { close(fd); return {}; }
        sent += w;
    }
    std::string total;
    char buf[4096];
    ssize_t r;
    while ((r = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, static_cast<size_t>(r));
    close(fd);
#else
    (void)path; (void)host; (void)port; (void)ts; (void)nonce; (void)sig;
    return {};
#endif

    // Parse status line
    size_t line1 = total.find("\r\n");
    if (line1 == std::string::npos) return {};
    std::string l1 = total.substr(0, line1);
    auto sp1 = l1.find(' ');
    if (sp1 == std::string::npos) return {};
    int status = std::atoi(l1.substr(sp1 + 1).c_str());
    if (status != 200) return {};
    size_t hdr_end = total.find("\r\n\r\n");
    if (hdr_end == std::string::npos) return {};
    return total.substr(hdr_end + 4);
}

std::string TtboxLicenseClient::api_post(const std::string& path,
                                         const std::string& body_json) {
#if !defined(__linux__) && !defined(__APPLE__) && !defined(_WIN32)
    (void)path; (void)body_json;
    return {};
#endif
    auto [host, port] = parse_url_host(server_url_);
    std::string ts = unix_timestamp_sec();
    std::string nonce = generate_nonce();
    std::string sig = sign_request("POST", path, ts, nonce, body_json);
    if (sig.empty()) return {};   // client_secret 未配置 ⇒ 拒签，不发请求（§3.3）

#if defined(_WIN32)
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return {};
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    std::snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host.c_str(), port_str, &hints, &res) != 0) {
        WSACleanup(); return {};
    }
    SOCKET fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd == INVALID_SOCKET) { freeaddrinfo(res); WSACleanup(); return {}; }
    if (connect(fd, res->ai_addr, static_cast<int>(res->ai_addrlen)) != 0) {
        closesocket(fd); freeaddrinfo(res); WSACleanup(); return {};
    }
    freeaddrinfo(res);
    std::ostringstream req;
    req << "POST " << path << " HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "Content-Length: " << body_json.size() << "\r\n";
    req << "X-App-Key: " << app_key_ << "\r\n";
    req << "X-Timestamp: " << ts << "\r\n";
    req << "X-Nonce: " << nonce << "\r\n";
    req << "X-Signature: " << sig << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    req << body_json;
    std::string reqs = req.str();
    send(fd, reqs.c_str(), static_cast<int>(reqs.size()), 0);
    std::string total;
    char buf[4096];
    int n;
    while ((n = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, n);
    closesocket(fd);
    WSACleanup();
#elif defined(__linux__) || defined(__APPLE__)
    struct addrinfo hints, *res = nullptr;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    std::snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host.c_str(), port_str, &hints, &res) != 0) return {};
    int fd = -1;
    for (struct addrinfo* p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        struct timeval tv;
        tv.tv_sec = 10; tv.tv_usec = 0;
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) return {};
    std::ostringstream req;
    req << "POST " << path << " HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "Content-Length: " << body_json.size() << "\r\n";
    req << "X-App-Key: " << app_key_ << "\r\n";
    req << "X-Timestamp: " << ts << "\r\n";
    req << "X-Nonce: " << nonce << "\r\n";
    req << "X-Signature: " << sig << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    req << body_json;
    std::string reqs = req.str();
    ssize_t sent = 0;
    while (sent < static_cast<ssize_t>(reqs.size())) {
        ssize_t w = send(fd, reqs.data() + sent, reqs.size() - sent, 0);
        if (w <= 0) { close(fd); return {}; }
        sent += w;
    }
    std::string total;
    char buf[4096];
    ssize_t r;
    while ((r = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, static_cast<size_t>(r));
    close(fd);
#else
    (void)path; (void)host; (void)port; (void)ts; (void)nonce;
    (void)sig; (void)body_json;
    return {};
#endif

    size_t line1 = total.find("\r\n");
    if (line1 == std::string::npos) return {};
    std::string l1 = total.substr(0, line1);
    auto sp1 = l1.find(' ');
    if (sp1 == std::string::npos) return {};
    int status = std::atoi(l1.substr(sp1 + 1).c_str());
    if (status != 200) return {};
    size_t hdr_end = total.find("\r\n\r\n");
    if (hdr_end == std::string::npos) return {};
    return total.substr(hdr_end + 4);
}

std::pair<int, int> TtboxLicenseClient::fetch_app_info() {
    int hb_interval = 60;
    int hb_timeout = 180;
    std::string body = api_get("/api/client/app-info?app_key=" + app_key_);
    if (body.empty()) return {hb_interval, hb_timeout};
    JsonParseResult pr = json_parse(body);
    if (!pr.ok) return {hb_interval, hb_timeout};
    const auto* data = pr.value.find("data");
    if (!data || !data->is_array() || data->as_array().empty())
        return {hb_interval, hb_timeout};
    const auto& first = data->as_array()[0];
    const auto* hi = first.find("heartbeatInterval");
    const auto* ht = first.find("heartbeatTimeout");
    if (hi) hb_interval = static_cast<int>(hi->as_number(kHeartbeatIntervalSecDefault));
    if (ht) hb_timeout = static_cast<int>(ht->as_number(kHeartbeatTimeoutSecDefault));
    return {hb_interval, hb_timeout};
}

bool TtboxLicenseClient::do_card_login(
    const std::string& card_key, const std::string& bind_device,
    LicenseStatus& out, std::string* err) {
    std::string body_json =
        "{\"appKey\":\"" + app_key_ +
        "\",\"cardKey\":\"" + card_key +
        "\",\"machineCode\":\"" + bind_device + "\"}";
    std::string resp = api_post("/api/client/card-login", body_json);
    if (resp.empty()) {
        if (err) *err = "card-login: empty response";
        return false;
    }
    JsonParseResult pr = json_parse(resp);
    if (!pr.ok) {
        if (err) *err = std::string("card-login: bad json: ") + pr.error;
        return false;
    }
    const auto* ok = pr.value.find("ok");
    if (!ok || !ok->as_bool()) {
        const auto* msg = pr.value.find("message");
        std::string msg_str = msg ? msg->as_string() : "unknown error";
        if (err) *err = "card-login: " + msg_str;
        out.state = LicenseState::kInvalidCard;
        out.last_error = msg_str;
        return true;  // request succeeded but card invalid
    }
    const auto& data = pr.value;
    const auto* token = data.find("clientToken");
    if (token) out.cached_token = token->as_string();
    const auto* expire = data.find("expireAt");
    if (expire) {
        // ★ 2026-09-23：expireAt 在契约里 **0 = 永久**（LicenseCard.hpp），
        //   所以「没带这个字段」和「带了但解析不出来」必须区别对待。
        //   后者原来被 `catch (...) {}` 静默吞掉、字段留 0 ⇒ 一张订阅卡会被当成
        //   永久卡 ⇒ 之后一断网就**永久**停在 kFallback（该状态在 LicenseGate 里放行）。
        //   解析失败按响应无效处理，绝不伪造"永久"。
        const std::string exp_str = expire->as_string("");
        if (expire->is_number()) {
            out.expire_unix_ms = expire->as_int(0);
        } else if (!exp_str.empty()) {
            int64_t v = 0;
            try {
                v = std::stoll(exp_str);
            } catch (...) {
                if (err) *err = "card-login: expireAt 不是合法整数: " + exp_str;
                out.state = LicenseState::kInvalidCard;
                out.last_error = "expireAt 无法解析为整数";
                return true;
            }
            out.expire_unix_ms = v;
        }
        // 空串 / 非标量 ⇒ 按"服务端未提供"处理，保持 0（= 永久）的既有语义
    }
    // M2：签名卡内容（features / plan / uiBrand）。此处不回落也不过滤 ——
    // 回落由 apply_check_result、闭集过滤由 to_snapshot 各自负责（单一职责）。
    parse_card_content(data, out);
    out.state = LicenseState::kValid;
    out.verified_at_ms = now_unix_ms();
    auto [hi, ht] = fetch_app_info();
    out.heartbeat_interval = hi;
    out.heartbeat_timeout = ht;
    out.next_check_ms = out.verified_at_ms + static_cast<int64_t>(hi) * 1000;
    return true;
}

bool TtboxLicenseClient::do_heartbeat(
    const std::string& token, const std::string& bind_device,
    LicenseStatus& out, std::string* err) {
    std::string body_json =
        "{\"machineCode\":\"" + bind_device +
        "\",\"clientVersion\":\"1.0.0\"}";
    auto [host, port] = parse_url_host(server_url_);
    std::string ts = unix_timestamp_sec();
    std::string nonce = generate_nonce();
    std::string sig = sign_request("POST", "/api/client/heartbeat", ts, nonce,
                                   body_json);
    if (sig.empty()) {   // client_secret 未配置 ⇒ 拒签，不发请求（§3.3）
        if (err) *err = "client_secret 未配置";
        out.state = LicenseState::kNetworkError;
        return false;
    }

    // Reuse the same TCP code path as api_post but add Authorization header
    // Inline a simple version: build request with extra header
#if defined(_WIN32)
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) {
        if (err) *err = "heartbeat: WSAStartup failed";
        out.state = LicenseState::kNetworkError;
        return false;
    }
    addrinfo hints{}, *res = nullptr;
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    std::snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host.c_str(), port_str, &hints, &res) != 0) {
        WSACleanup();
        if (err) *err = "heartbeat: DNS resolve failed";
        out.state = LicenseState::kNetworkError;
        return false;
    }
    SOCKET fd = socket(res->ai_family, res->ai_socktype, res->ai_protocol);
    if (fd == INVALID_SOCKET) { freeaddrinfo(res); WSACleanup(); return false; }
    if (connect(fd, res->ai_addr, static_cast<int>(res->ai_addrlen)) != 0) {
        closesocket(fd); freeaddrinfo(res); WSACleanup(); return false;
    }
    freeaddrinfo(res);
    std::ostringstream req;
    req << "POST /api/client/heartbeat HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "Content-Length: " << body_json.size() << "\r\n";
    req << "X-App-Key: " << app_key_ << "\r\n";
    req << "X-Timestamp: " << ts << "\r\n";
    req << "X-Nonce: " << nonce << "\r\n";
    req << "X-Signature: " << sig << "\r\n";
    req << "Authorization: Bearer " << token << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    req << body_json;
    std::string reqs = req.str();
    send(fd, reqs.c_str(), static_cast<int>(reqs.size()), 0);
    std::string total;
    char buf[4096];
    int n;
    while ((n = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, n);
    closesocket(fd);
    WSACleanup();
#elif defined(__linux__) || defined(__APPLE__)
    struct addrinfo hints, *res = nullptr;
    std::memset(&hints, 0, sizeof(hints));
    hints.ai_family = AF_UNSPEC;
    hints.ai_socktype = SOCK_STREAM;
    char port_str[16];
    std::snprintf(port_str, sizeof(port_str), "%d", port);
    if (getaddrinfo(host.c_str(), port_str, &hints, &res) != 0) {
        if (err) *err = "heartbeat: DNS failed";
        out.state = LicenseState::kNetworkError;
        return false;
    }
    int fd = -1;
    for (struct addrinfo* p = res; p; p = p->ai_next) {
        fd = socket(p->ai_family, p->ai_socktype, p->ai_protocol);
        if (fd < 0) continue;
        struct timeval tv;
        tv.tv_sec = 10; tv.tv_usec = 0;
        setsockopt(fd, SOL_SOCKET, SO_SNDTIMEO, &tv, sizeof(tv));
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, sizeof(tv));
        if (connect(fd, p->ai_addr, p->ai_addrlen) == 0) break;
        close(fd); fd = -1;
    }
    freeaddrinfo(res);
    if (fd < 0) {
        if (err) *err = "heartbeat: connect failed";
        out.state = LicenseState::kNetworkError;
        return false;
    }
    std::ostringstream req;
    req << "POST /api/client/heartbeat HTTP/1.1\r\n";
    req << "Host: " << host << "\r\n";
    req << "Content-Type: application/json\r\n";
    req << "Content-Length: " << body_json.size() << "\r\n";
    req << "X-App-Key: " << app_key_ << "\r\n";
    req << "X-Timestamp: " << ts << "\r\n";
    req << "X-Nonce: " << nonce << "\r\n";
    req << "X-Signature: " << sig << "\r\n";
    req << "Authorization: Bearer " << token << "\r\n";
    req << "Connection: close\r\n";
    req << "\r\n";
    req << body_json;
    std::string reqs = req.str();
    ssize_t sent = 0;
    while (sent < static_cast<ssize_t>(reqs.size())) {
        ssize_t w = send(fd, reqs.data() + sent, reqs.size() - sent, 0);
        if (w <= 0) { close(fd); if (err) *err = "heartbeat: send failed"; out.state = LicenseState::kNetworkError; return false; }
        sent += w;
    }
    std::string total;
    char buf[4096];
    ssize_t r;
    while ((r = recv(fd, buf, sizeof(buf), 0)) > 0)
        total.append(buf, static_cast<size_t>(r));
    close(fd);
#else
    (void)token; (void)bind_device; (void)out; (void)err;
    return false;
#endif

    size_t line1 = total.find("\r\n");
    if (line1 == std::string::npos) {
        if (err) *err = "heartbeat: no response";
        out.state = LicenseState::kNetworkError;
        return false;
    }
    std::string l1 = total.substr(0, line1);
    auto sp1 = l1.find(' ');
    if (sp1 == std::string::npos) {
        if (err) *err = "heartbeat: bad status line";
        out.state = LicenseState::kNetworkError;
        return false;
    }
    int status = std::atoi(l1.substr(sp1 + 1).c_str());
    if (status != 200) {
        if (err) *err = "heartbeat: HTTP " + std::to_string(status);
        out.state = LicenseState::kNetworkError;
        return false;
    }
    size_t hdr_end = total.find("\r\n\r\n");
    if (hdr_end == std::string::npos) {
        if (err) *err = "heartbeat: no body";
        out.state = LicenseState::kNetworkError;
        return false;
    }
    std::string body = total.substr(hdr_end + 4);
    JsonParseResult pr = json_parse(body);
    if (!pr.ok) {
        if (err) *err = std::string("heartbeat: bad json: ") + pr.error;
        out.state = LicenseState::kInvalidCard;
        return true;
    }
    const auto* ok = pr.value.find("ok");
    if (!ok || !ok->as_bool()) {
        const auto* msg = pr.value.find("message");
        std::string msg_str = msg ? msg->as_string() : "heartbeat failed";
        if (err) *err = "heartbeat: " + msg_str;
        out.state = LicenseState::kExpired;
        out.last_error = msg_str;
        return true;
    }
    // Heartbeat success: update token and expiry
    const auto* t = pr.value.find("clientToken");
    if (t) out.cached_token = t->as_string();
    const auto* exp = pr.value.find("expireAt");
    if (exp) {
        // 与上面 card-login 同口径：expireAt = 0 是契约里的"永久"，
        // 但**解析失败**不能静默留 0（否则订阅卡会被误判成永久卡，断网后永不退出
        // fail-open）。详见 TtboxLicenseClient 中同名的另一处注释。
        const std::string exp_str = exp->as_string("");
        if (exp->is_number()) {
            out.expire_unix_ms = exp->as_int(0);
        } else if (!exp_str.empty()) {
            int64_t v = 0;
            try {
                v = std::stoll(exp_str);
            } catch (...) {
                if (err) *err = "heartbeat: expireAt 不是合法整数: " + exp_str;
                out.state = LicenseState::kInvalidCard;
                out.last_error = "expireAt 无法解析为整数";
                return true;
            }
            out.expire_unix_ms = v;
        }
    }
    const auto* hi = pr.value.find("heartbeatInterval");
    if (hi) out.heartbeat_interval = static_cast<int>(hi->as_number(60));
    // M2：续期响应同样携带签名卡内容（服务端契约要求，见 parse_card_content 说明）。
    parse_card_content(pr.value, out);
    out.state = LicenseState::kValid;
    out.verified_at_ms = now_unix_ms();
    out.next_check_ms = out.verified_at_ms +
                        static_cast<int64_t>(out.heartbeat_interval) * 1000;
    return true;
}

bool TtboxLicenseClient::verify_once(
    const std::string& card, const std::string& bind_device,
    LicenseStatus& out_status, std::string* err_message) {
    // Preserve previous cached token from caller
    std::string prev_token = out_status.cached_token;
    out_status = LicenseStatus{};
    if (client_secret_.empty()) {   // §3.3：空 secret 拒签（网络类失败 ⇒ fail-open）
        out_status.state = LicenseState::kNetworkError;
        if (err_message) *err_message = "client_secret 未配置（拒绝签名）";
        return false;
    }
    if (card.empty()) {
        out_status.state = LicenseState::kInvalidCard;
        out_status.last_error = "card empty";
        if (err_message) *err_message = out_status.last_error;
        return false;
    }
    // If we have a valid cached token and card is a license key, try heartbeat first
    if (!prev_token.empty() && card.find("LS-") == 0) {
        auto [hi, ht] = fetch_app_info();
        out_status.heartbeat_interval = hi;
        out_status.heartbeat_timeout = ht;
        bool ok = do_heartbeat(prev_token, bind_device, out_status, err_message);
        if (ok && out_status.state == LicenseState::kValid) return true;
        // Heartbeat failed (token expired / network error) → fall back to card-login
    }
    // First login: card-login
    return do_card_login(card, bind_device, out_status, err_message);
}

void TtboxLicenseClient::override_server(const std::string& server_url) {
    if (!server_url.empty()) server_url_ = server_url;
}

void TtboxLicenseClient::override_credentials(const std::string& app_key,
                                              const std::string& client_secret) {
    if (!app_key.empty()) app_key_ = app_key;
    if (!client_secret.empty()) client_secret_ = client_secret;
}

}  // namespace ttbox::core::auth
