// mouse_control.cpp — TTBOX usb-proxy mouse_control 协议层（自研 0x4F50 协议）
// 分块编写：part1 全局量 + 报文编解码 + 服务骨架。
#include "mouse_control.hpp"

#include <sys/socket.h>
#include <sys/un.h>
#include <sys/stat.h>
#include <unistd.h>
#include <pthread.h>
#include <sched.h>
#include <atomic>
#include <cerrno>
#include <cstring>
#include <chrono>
#include <cstdio>
#include <algorithm>
#include <vector>
#include <fstream>
#include <jsoncpp/json/json.h>
#include "misc.h"

extern bool please_stop_ep0;

// RT：realtime=fifo:98 + CPU affinity（默认最后一颗核，RK3588 大核）
// 两者都可用环境变量覆盖：USB_PROXY_MOUSE_CONTROL_RT_PRIORITY（0=关）、
// USB_PROXY_MOUSE_CONTROL_CPU_AFFINITY（-1=不绑）。
void apply_rt_thread_policy() {
    int ncpu = static_cast<int>(sysconf(_SC_NPROCESSORS_ONLN));
    int target = ncpu - 1;  // 板端 run_usb_proxy.sh 用 cpu_affinity=7（8 核的最后核）
    usbproxy_rt_apply("MOUSE_CONTROL", 98,
                      "USB_PROXY_MOUSE_CONTROL_CPU_AFFINITY", target);
}

namespace ttbox_usbproxy {

MouseControlState g_state;
GadgetConfig g_gadget_config;

namespace {

constexpr int kMaxPayload = 4096;

struct ServerThreads {
    pthread_t cmd_thread;
    pthread_t event_thread;
    int cmd_listen_fd = -1;
    int event_listen_fd = -1;
    std::atomic<bool> running{false};
} g_srv;

int64_t now_us() {
    return std::chrono::duration_cast<std::chrono::microseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

int64_t now_ns() {
    return std::chrono::duration_cast<std::chrono::nanoseconds>(
        std::chrono::steady_clock::now().time_since_epoch()).count();
}

// 编码头部
void encode_header(uint8_t* buf, uint8_t type, uint32_t rid) {
    PacketHeader h{kMagic, kVersion, type, rid};
    std::memcpy(buf, &h, sizeof(h));
}

// 发送完整报文到 fd
bool send_packet(int fd, uint8_t type, uint32_t rid,
                 const void* payload, size_t plen) {
    uint8_t buf[sizeof(PacketHeader) + kMaxPayload];
    if (plen > kMaxPayload) return false;
    encode_header(buf, type, rid);
    if (plen) std::memcpy(buf + sizeof(PacketHeader), payload, plen);
    size_t total = sizeof(PacketHeader) + plen;
    ssize_t sent = ::send(fd, buf, total, MSG_NOSIGNAL);
    return sent == static_cast<ssize_t>(total);
}

// 发送错误响应
void send_error(int fd, uint32_t rid, uint16_t code, const char* text) {
    uint8_t payload[512];
    size_t tlen = std::strlen(text);
    if (tlen > 500) tlen = 500;
    std::memcpy(payload, &code, 2);
    uint16_t sz = static_cast<uint16_t>(tlen);
    std::memcpy(payload + 2, &sz, 2);
    std::memcpy(payload + 4, text, tlen);
    send_packet(fd, kErrorResp, rid, payload, 4 + tlen);
}

// ── GET_CONFIG_RESP 编码（字段顺序一致）──
void encode_config_payload(std::vector<uint8_t>& out) {
    const GadgetConfig& c = g_gadget_config;
    out.reserve(64 + c.manufacturer.size() + c.product.size() +
                c.serial.size() + c.configuration.size() +
                c.hid_report_desc_hex.size());
    auto push16 = [&](uint16_t v) {
        out.push_back(static_cast<uint8_t>(v & 0xFF));
        out.push_back(static_cast<uint8_t>(v >> 8));
    };
    auto push8 = [&](uint8_t v) { out.push_back(v); };
    auto push_str = [&](const std::string& s) {
        uint16_t n = static_cast<uint16_t>(s.size());
        push16(n);
        out.insert(out.end(), s.begin(), s.end());
    };
    push16(c.usb_vid);
    push16(c.usb_pid);
    push16(c.usb_bcd_usb);
    push16(c.usb_bcd_device);
    push8(c.usb_device_class);
    push8(c.usb_device_subclass);
    push8(c.usb_device_protocol);
    push16(c.usb_max_power);   // 协议 <HHHHBBBHBBBB：max_power 是 u16
    push8(c.hid_protocol);
    push8(c.hid_subclass);
    push8(c.hid_report_length);
    push8(c.hid_interval);
    push_str(c.manufacturer);
    push_str(c.product);
    push_str(c.serial);
    push_str(c.configuration);
    push_str(c.hid_report_desc_hex);
}

// ── SET_CONFIG 解码（协议逐字节对应）──
// 输入：payload 指向固定字段+字符串区（不含 apply_now 字节）
bool decode_config_payload(const uint8_t* payload, size_t plen) {
    // ★ 2026-09-23：原来截断时静默返回 0/空串却继续解析，函数**恒返回 true**
    //   ⇒ 畸形 SET_CONFIG 会把 gadget-config.json 写成一堆空字段，
    //   重启后设备直接无法枚举；而且下面那句 "bad set-config payload" 永远走不到。
    //   改成任何一次越界读取都置 ok=false，由调用方拒绝整个报文。
    bool ok = true;
    auto rd16 = [&](size_t& off) -> uint16_t {
        uint16_t v = 0;
        if (off + 2 > plen) { ok = false; return 0; }
        v = static_cast<uint16_t>(payload[off] | (payload[off + 1] << 8));
        off += 2;
        return v;
    };
    auto rd8 = [&](size_t& off) -> uint8_t {
        if (off + 1 > plen) { ok = false; return 0; }
        return payload[off++];
    };
    auto rd_str = [&](size_t& off) -> std::string {
        if (off + 2 > plen) { ok = false; return ""; }
        uint16_t n = static_cast<uint16_t>(payload[off] | (payload[off + 1] << 8));
        off += 2;
        if (off + n > plen) { ok = false; return ""; }
        std::string s(reinterpret_cast<const char*>(payload + off), n);
        off += n;
        return s;
    };

    size_t off = 0;
    GadgetConfig c;
    c.usb_vid = rd16(off);
    c.usb_pid = rd16(off);
    c.usb_bcd_usb = rd16(off);
    c.usb_bcd_device = rd16(off);
    c.usb_device_class = rd8(off);
    c.usb_device_subclass = rd8(off);
    c.usb_device_protocol = rd8(off);
    c.usb_max_power = rd16(off);   // 协议 <HHHHBBBHBBBB：u16
    c.hid_protocol = rd8(off);
    c.hid_subclass = rd8(off);
    c.hid_report_length = rd8(off);
    c.hid_interval = rd8(off);
    c.manufacturer = rd_str(off);
    c.product = rd_str(off);
    c.serial = rd_str(off);
    c.configuration = rd_str(off);
    c.hid_report_desc_hex = rd_str(off);
    // ★ 任一字段读取越界 ⇒ 整包作废。绝不用半截配置覆盖 gadget-config.json，
    //   否则重启后 USB 描述符是空的，设备直接枚举不上。
    if (!ok) return false;
    g_gadget_config = c;
    return true;
}

// ── SET_CONFIG 持久化：写回 gadget-config.json（重启后生效）──
int persist_gadget_config() {
    const char* cfg_path = getenv("USB_PROXY_GADGET_CONFIG_FILE");
    if (!cfg_path) cfg_path = "gadget-config.json";
    Json::Value root;
    const GadgetConfig& c = g_gadget_config;
    root["usb_vid"] = c.usb_vid;
    root["usb_pid"] = c.usb_pid;
    root["usb_bcd_usb"] = c.usb_bcd_usb;
    root["usb_bcd_device"] = c.usb_bcd_device;
    root["usb_device_class"] = c.usb_device_class;
    root["usb_device_subclass"] = c.usb_device_subclass;
    root["usb_device_protocol"] = c.usb_device_protocol;
    root["usb_max_power"] = c.usb_max_power;
    root["hid_protocol"] = c.hid_protocol;
    root["hid_subclass"] = c.hid_subclass;
    root["hid_report_length"] = c.hid_report_length;
    root["hid_interval"] = c.hid_interval;
    root["usb_manufacturer"] = c.manufacturer;
    root["usb_product"] = c.product;
    root["usb_serial"] = c.serial;
    root["usb_configuration"] = c.configuration;
    root["hid_report_desc_hex"] = c.hid_report_desc_hex;
    std::ofstream ofs(cfg_path, std::ios::trunc);
    if (!ofs.is_open()) {
        fprintf(stderr, "persist_gadget_config: cannot open %s\n", cfg_path);
        return -1;
    }
    ofs << root.toStyledString();
    ofs.close();
    printf("set-config saved: vid=%04x pid=%04x\n", c.usb_vid, c.usb_pid);
    return 0;
}

// ── 位移累积（钳位 + 饱和）────────────────────────────────────────
// ★ 2026-09-23：原实现 `pending_dx.fetch_add(dx)` 无任何钳位 ⇒
//   ① int32 溢出是**未定义行为**；② 客户端连续发大值会让累积位移无限增长，
//   表现为鼠标"飞"出去且几十秒内回不来（累积量要慢慢消费完）。
//   单次值夹到 HID 报告能表达的范围，累积值用 64 位中间量算出后再饱和夹回 int32。
static constexpr int32_t kMoveStepLimit = 32767;      // 单次位移上界
static constexpr int32_t kPendingLimit = 1 << 20;     // 累积位移上界 ±1048575

static void add_pending(std::atomic<int32_t>& acc, int32_t delta) {
    if (delta > kMoveStepLimit) delta = kMoveStepLimit;
    else if (delta < -kMoveStepLimit) delta = -kMoveStepLimit;
    int32_t cur = acc.load();
    for (;;) {
        int64_t s = static_cast<int64_t>(cur) + static_cast<int64_t>(delta);
        if (s > kPendingLimit) s = kPendingLimit;
        else if (s < -kPendingLimit) s = -kPendingLimit;
        const int32_t want = static_cast<int32_t>(s);
        if (acc.compare_exchange_weak(cur, want)) return;
    }
}

// ── 命令分发：处理单个 cmd.sock 连接 ────────────────────────────
void handle_cmd_connection(int fd) {
    uint8_t buf[sizeof(PacketHeader) + kMaxPayload];
    for (;;) {
        ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
        if (n <= 0) break;
        if (n < static_cast<ssize_t>(sizeof(PacketHeader))) continue;
        PacketHeader h;
        std::memcpy(&h, buf, sizeof(h));
        if (h.magic != kMagic || h.version != kVersion) {
            send_error(fd, h.request_id, 1, "bad magic/version");
            continue;
        }
        const uint8_t* payload = buf + sizeof(PacketHeader);
        size_t plen = static_cast<size_t>(n) - sizeof(PacketHeader);

        switch (h.type) {
        case kPingReq:
            send_packet(fd, kPingResp, h.request_id, nullptr, 0);
            break;

        case kMoveCmd: {
            // payload <iii dx,dy,wheel
            if (plen < 12) { send_error(fd, h.request_id, 2, "short move"); break; }
            int32_t dx, dy, wheel;
            std::memcpy(&dx, payload, 4);
            std::memcpy(&dy, payload + 4, 4);
            std::memcpy(&wheel, payload + 8, 4);
            add_pending(g_state.pending_dx, dx);
            add_pending(g_state.pending_dy, dy);
            add_pending(g_state.pending_wheel, wheel);
            g_state.move_count.fetch_add(1);
            g_state.last_move_ts_us.store(now_us());
            break;
        }

        case kButtonCmd: {
            // payload <BB button, action
            if (plen < 2) { send_error(fd, h.request_id, 3, "short button"); break; }
            uint8_t button = payload[0];
            uint8_t action = payload[1];
            // button 从 1 开始且当前协议只暴露 8 个按钮；先校验再移位，
            // 避免 button=0/超范围导致未定义行为或污染整个掩码。
            if (button < 1 || button > 8 ||
                (action != kActDown && action != kActUp && action != kActClick)) {
                send_error(fd, h.request_id, 3, "invalid button/action");
                break;
            }
            uint8_t bit = static_cast<uint8_t>(1u << (button - 1));
            uint8_t cur = g_state.button_mask.load();
            uint8_t next = cur;
            if (action == kActDown || action == kActClick) next |= bit;
            else if (action == kActUp) next &= static_cast<uint8_t>(~bit);
            g_state.button_mask.store(next);
            break;
        }

        case kGetStateReq: {
            // payload <BQ mask, timestamp_ns
            uint8_t resp[9] = {0};
            resp[0] = g_state.button_mask.load();
            int64_t ts = now_ns();
            std::memcpy(resp + 1, &ts, 8);
            send_packet(fd, kGetStateResp, h.request_id, resp, sizeof(resp));
            break;
        }

        case kGetConfigReq: {
            std::vector<uint8_t> cfg;
            encode_config_payload(cfg);
            send_packet(fd, kGetConfigResp, h.request_id, cfg.data(), cfg.size());
            break;
        }

        case kSetConfigReq: {
            // payload: <B apply_now + encode_config
            if (plen < 1) { send_error(fd, h.request_id, 5, "short set-config"); break; }
            bool apply_now = payload[0] != 0;
            if (decode_config_payload(payload + 1, plen - 1)) {
                // 持久化到 gadget-config.json（重启后生效）
                if (persist_gadget_config() != 0) {
                    send_error(fd, h.request_id, 6, "config save failed");
                    break;
                }
                send_packet(fd, kSetConfigResp, h.request_id, "\x01", 1);
                if (apply_now) {
                    // 设计：usb-proxy 重启 + Windows 重新枚举。
                    // 直接退出进程，由 systemd Restart=always 以新配置拉起。
                    fprintf(stderr, "set-config apply-now: restarting to re-enumerate\n");
                    fflush(stdout);
                    fflush(stderr);
                    _exit(0);
                }
            } else {
                send_error(fd, h.request_id, 5, "bad set-config payload");
            }
            break;
        }

        default:
            send_error(fd, h.request_id, 4, "unsupported command");
            break;
        }
    }
    ::close(fd);
}

// ── event.sock 订阅者处理：SUBSCRIBE → ACK → 推送状态快照 ──────────
void handle_event_connection(int fd) {
    uint8_t buf[sizeof(PacketHeader) + kMaxPayload];
    ssize_t n = ::recv(fd, buf, sizeof(buf), 0);
    if (n < static_cast<ssize_t>(sizeof(PacketHeader))) { ::close(fd); return; }
    PacketHeader h;
    std::memcpy(&h, buf, sizeof(h));
    if (h.magic != kMagic || h.version != kVersion || h.type != kSubscribeReq) {
        ::close(fd);
        return;
    }
    send_packet(fd, kSubscribeAck, h.request_id, nullptr, 0);
    {
        std::lock_guard<std::mutex> lk(g_state.subscribers_mutex);
        g_state.subscribers.push_back(fd);
    }
    // 保持连接；发送失败时移除订阅者。周期性推送 STATE_SNAPSHOT（<BQ mask, timestamp_ns）
    for (;;) {
        uint8_t ev[9] = {0};
        ev[0] = g_state.button_mask.load();
        int64_t ts = now_ns();
        std::memcpy(ev + 1, &ts, 8);
        if (!send_packet(fd, kStateSnapshot, 0, ev, sizeof(ev))) break;
        ::usleep(100000);  // 100ms 周期快照（订阅循环兼容）
    }
    {
        std::lock_guard<std::mutex> lk(g_state.subscribers_mutex);
        auto it = std::find(g_state.subscribers.begin(), g_state.subscribers.end(), fd);
        if (it != g_state.subscribers.end()) g_state.subscribers.erase(it);
    }
    ::close(fd);
}

// ── 连接处理线程（每连接一线程，长连接不再阻塞 accept 循环）─────
void* cmd_connection_entry(void* arg) {
    int fd = static_cast<int>(reinterpret_cast<intptr_t>(arg));
    handle_cmd_connection(fd);
    return nullptr;
}

void* event_connection_entry(void* arg) {
    int fd = static_cast<int>(reinterpret_cast<intptr_t>(arg));
    handle_event_connection(fd);
    return nullptr;
}

static bool spawn_connection(void* (*entry)(void*), int fd) {
    pthread_t tid;
    if (pthread_create(&tid, nullptr, entry,
                       reinterpret_cast<void*>(static_cast<intptr_t>(fd))) != 0) {
        fprintf(stderr, "pthread_create(connection) failed: %s\n", strerror(errno));
        ::close(fd);
        return false;
    }
    pthread_detach(tid);
    return true;
}

// ── 控制口对端身份校验（2026-09-23）──────────────────────────────
//   cmd.sock 上任何能连上的进程都能发 SET_CONFIG + apply_now 把 usb-proxy 直接
//   _exit(0) 杀掉（靠 systemd Restart=always 拉起），并把 gadget-config.json
//   写坏（重启后 Windows 无法枚举设备）。此前唯一的防护是文件权限 0660
//   ⇒ 同组用户、以及任何拿到 ttbox 组身份的进程都能为所欲为。
//
//   这里再加一道**内核级**对端凭据校验（SO_PEERCRED，由内核填、无法伪造）：
//   只放行 root、与本进程同 uid、或属本进程主组（ttbox）的对端。
//   合法客户端正好落在这三类里：Core 以 root 跑、Web 面板以 ttbox 组跑。
static bool cmd_peer_allowed(int fd) {
    struct ucred cred{};
    socklen_t len = sizeof(cred);
    if (::getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &len) != 0) {
        fprintf(stderr, "cmd.sock: 取不到对端凭据，拒绝连接: %s\n", strerror(errno));
        return false;
    }
    if (cred.uid == 0) return true;                 // root（Core）
    if (cred.uid == ::getuid()) return true;        // 本进程同 uid
    if (cred.gid == ::getgid()) return true;        // 本进程主组（ttbox，Web 面板）
    fprintf(stderr, "cmd.sock: 拒绝未授权对端 uid=%u gid=%u\n",
            static_cast<unsigned>(cred.uid), static_cast<unsigned>(cred.gid));
    return false;
}

// ── 监听线程：accept 循环 ────────────────────────────────────────
void* cmd_listen_loop(void* arg) {
    apply_rt_thread_policy();  // RT 线程
    int listen_fd = *static_cast<int*>(arg);
    while (g_srv.running.load()) {
        int cfd = ::accept(listen_fd, nullptr, nullptr);
        if (cfd < 0) {
            if (errno == EINTR) continue;
            if (errno == EBADF) break;
            ::usleep(50000);
            continue;
        }
        // ★ 先验身份再建处理线程：未授权对端直接关掉，连一个字节都不读。
        if (!cmd_peer_allowed(cfd)) {
            ::close(cfd);
            continue;
        }
        spawn_connection(cmd_connection_entry, cfd);
    }
    return nullptr;
}

void* event_listen_loop(void* arg) {
    apply_rt_thread_policy();  // RT 线程
    int listen_fd = *static_cast<int*>(arg);
    while (g_srv.running.load()) {
        int cfd = ::accept(listen_fd, nullptr, nullptr);
        if (cfd < 0) {
            if (errno == EINTR) continue;
            if (errno == EBADF) break;
            ::usleep(50000);
            continue;
        }
        spawn_connection(event_connection_entry, cfd);
    }
    return nullptr;
}

int create_listen_socket(const char* path) {
    // Do not unlink a socket that is already being served.  Compatible socket
    // names are shared on some boards; replacing a live socket here would
    // silently steal another active mouse-control channel.
    if (::access(path, F_OK) == 0) {
        int probe = ::socket(AF_UNIX, SOCK_SEQPACKET, 0);
        if (probe >= 0) {
            sockaddr_un probe_addr{};
            probe_addr.sun_family = AF_UNIX;
            std::strncpy(probe_addr.sun_path, path, sizeof(probe_addr.sun_path) - 1);
            if (::connect(probe, reinterpret_cast<const sockaddr*>(&probe_addr),
                          sizeof(probe_addr)) == 0) {
                ::close(probe);
                fprintf(stderr, "refusing to replace active socket: %s\n", path);
                return -1;
            }
            ::close(probe);
        }
        ::unlink(path);  // stale socket left by an unclean shutdown
    }
    int fd = ::socket(AF_UNIX, SOCK_SEQPACKET, 0);
    if (fd < 0) { perror("socket"); return -1; }
    sockaddr_un addr{};
    addr.sun_family = AF_UNIX;
    std::strncpy(addr.sun_path, path, sizeof(addr.sun_path) - 1);
    if (::bind(fd, reinterpret_cast<const sockaddr*>(&addr), sizeof(addr)) < 0) {
        perror("bind");
        ::close(fd);
        return -1;
    }
    // 权限收紧（T02）：socket 位于 /run/ttbox-mouse-passthrough（usbproxy 的
    // RuntimeDirectory）。usbproxy 以 root:ttbox 运行，0660 = 同组（ttbox，即 Core
    // 的运行组）可读写。此前的 0666 是全局可写，任意本地用户可注入鼠标指令。
    ::chmod(path, 0660);
    if (::listen(fd, 8) < 0) {
        perror("listen");
        ::close(fd);
        return -1;
    }
    return fd;
}

}  // namespace

// ── 公开接口：启动 / 停止 ────────────────────────────────────────
int mouse_control_start(const std::string& cmd_socket,
                        const std::string& event_socket,
                        bool synthetic) {
    if (g_srv.running.load()) return 0;
    g_state.synthetic_mode.store(synthetic);
    g_state.mouse_control_enabled.store(true);

    g_srv.cmd_listen_fd = create_listen_socket(cmd_socket.c_str());
    if (g_srv.cmd_listen_fd < 0) return -1;
    g_srv.event_listen_fd = create_listen_socket(event_socket.c_str());
    if (g_srv.event_listen_fd < 0) {
        ::close(g_srv.cmd_listen_fd);
        ::unlink(cmd_socket.c_str());
        g_srv.cmd_listen_fd = -1;
        return -1;
    }

    g_srv.running.store(true);
    if (pthread_create(&g_srv.cmd_thread, nullptr, cmd_listen_loop,
                       &g_srv.cmd_listen_fd) != 0) {
        g_srv.running.store(false);
        ::close(g_srv.cmd_listen_fd);
        ::close(g_srv.event_listen_fd);
        return -1;
    }
    if (pthread_create(&g_srv.event_thread, nullptr, event_listen_loop,
                       &g_srv.event_listen_fd) != 0) {
        g_srv.running.store(false);
        ::close(g_srv.cmd_listen_fd);
        ::close(g_srv.event_listen_fd);
        return -1;
    }
    printf("mouse_control: cmd=%s event=%s synthetic=%d\n",
           cmd_socket.c_str(), event_socket.c_str(), synthetic ? 1 : 0);
    return 0;
}

void mouse_control_stop() {
    if (!g_srv.running.load()) return;
    g_srv.running.store(false);
    if (g_srv.cmd_listen_fd >= 0) ::close(g_srv.cmd_listen_fd);
    if (g_srv.event_listen_fd >= 0) ::close(g_srv.event_listen_fd);
    pthread_join(g_srv.cmd_thread, nullptr);
    pthread_join(g_srv.event_thread, nullptr);
    g_srv.cmd_listen_fd = -1;
    g_srv.event_listen_fd = -1;
    {
        std::lock_guard<std::mutex> lk(g_state.subscribers_mutex);
        for (int fd : g_state.subscribers) ::close(fd);
        g_state.subscribers.clear();
    }
    g_state.mouse_control_enabled.store(false);
}

// ── 布局诊断（2026-09-24 重写：从"猜偏移"改为"解析描述符"）────────
//
// 旧版把报告布局写死成罗技 c53f（rid=2、buttons@1..2、X@3..4、Y@5..6、len=9）。
// 1.5.44 只给**长度**加了自适应，偏移没动 ⇒ 现场那只鼠标
//   `[0]=rid(0x02) [1..2]=X int16 [3..4]=Y int16 [5]=wheel [6]=pan`
// 被整体错位写入：
//   · AI 的 **Y 位移被写进 wheel 字节** ⇒ 主机每帧收到一次滚轮 ⇒ 游戏疯狂切枪；
//   · 按键掩码读的是 `data[1] | data[2]<<8` = **X 位移的低 16 位** ⇒ 掩码恒被污染
//     （现场实测 0xff = 五个键全亮）⇒ 自瞄热键闸门形同虚设、"不按它也在跑"。
//
// 现在：每个接口的 HID 报告描述符解析出**真实字段位置**再读写；解析不出来就一个字
// 都不碰（fail-closed）。下面的计数是"为什么没动"的唯一说法。
enum LayoutReject {
    kRejectNoLayout = 0,   // 该接口还没拿到可用布局（描述符没收到 / 畸形 / 无 X-Y 与 buttons）
    kRejectNoXy = 1,       // 这份报告（report_id）里没有 X/Y —— 不是位移报告
    kRejectLen = 2,        // 报告长度与布局对不上（放不下字段）
    kRejectField = 3,      // 字段位宽/对齐不安全，或与 buttons/wheel 字节区间重叠
    kRejectNoButtons = 4,  // 这份报告里没有 buttons 字段 —— 不是按键报告
};

struct LayoutDiag {
    std::atomic<uint64_t> merge_ok{0};
    std::atomic<uint64_t> reject_no_layout{0};
    std::atomic<uint64_t> reject_no_xy{0};
    std::atomic<uint64_t> reject_len{0};
    std::atomic<uint64_t> reject_field{0};
    std::atomic<uint64_t> reject_no_buttons{0};
    std::atomic<int> first_logs{0};
    std::atomic<int64_t> last_summary_us{0};
};

static LayoutDiag g_layout_diag;

static void count_reject(LayoutReject kind) {
    switch (kind) {
    case kRejectNoLayout:  g_layout_diag.reject_no_layout.fetch_add(1); break;
    case kRejectNoXy:      g_layout_diag.reject_no_xy.fetch_add(1); break;
    case kRejectLen:       g_layout_diag.reject_len.fetch_add(1); break;
    case kRejectField:     g_layout_diag.reject_field.fetch_add(1); break;
    default:               g_layout_diag.reject_no_buttons.fetch_add(1); break;
    }
    // 前 5 次逐条给 kind，之后每 5s 一条汇总（1kHz 报告下不能刷爆 journal）
    if (g_layout_diag.first_logs.fetch_add(1) < 5) {
        fprintf(stderr,
                "[mouse_control][LAYOUT] 放弃本次读写 kind=%d"
                "（0=无布局 1=非位移报告 2=长度不符 3=字段不安全 4=非按键报告）\n",
                static_cast<int>(kind));
        return;
    }
    const int64_t now = now_us();
    int64_t last = g_layout_diag.last_summary_us.load();
    if (now - last >= 5000000 &&
        g_layout_diag.last_summary_us.compare_exchange_strong(last, now)) {
        fprintf(stderr,
                "[mouse_control][LAYOUT] 5s 汇总：merge_ok=%llu 无布局=%llu 非位移报告=%llu "
                "长度不符=%llu 字段不安全=%llu 非按键报告=%llu\n",
                (unsigned long long)g_layout_diag.merge_ok.load(),
                (unsigned long long)g_layout_diag.reject_no_layout.load(),
                (unsigned long long)g_layout_diag.reject_no_xy.load(),
                (unsigned long long)g_layout_diag.reject_len.load(),
                (unsigned long long)g_layout_diag.reject_field.load(),
                (unsigned long long)g_layout_diag.reject_no_buttons.load());
    }
}

// 取"这份报告"对应的布局。
//   · 描述符里出现过 Report ID ⇒ 报告首字节是 report_id，其后才是数据；
//   · 否则整份报告都是数据（按 report_id = 0 找）。
// 返回 nullptr = 该接口不可用 / 这个 report_id 没有布局 ⇒ 调用方必须放弃。
static const HidReportLayout* layout_for_report(uint8_t interface_number,
                                                const uint8_t* data, uint32_t len,
                                                const uint8_t** body, size_t* body_len) {
    if (data == nullptr || len == 0 || interface_number >= 8) return nullptr;
    HidMouseDescriptor desc;
    {
        std::lock_guard<std::mutex> lk(g_state.layout_mutex);
        const InterfaceLayout& il = g_state.iface_layouts[interface_number];
        if (!il.ready || !il.usable) return nullptr;
        desc = il.desc;
    }

    int rid = 0;
    size_t off = 0;
    if (desc.uses_report_ids) {
        rid = data[0];
        off = 1;
    }
    if (static_cast<size_t>(len) <= off) return nullptr;
    const HidReportLayout* lay = desc.find(rid);
    if (lay == nullptr) return nullptr;
    if (body) *body = data + off;
    if (body_len) *body_len = static_cast<size_t>(len) - off;
    return lay;
}

// 收到物理设备某接口的 HID 报告描述符 → 解析并缓存（fail-closed 的前提）。
void mouse_control_set_report_descriptor(uint8_t interface_number,
                                        const uint8_t* desc, uint32_t len) {
    if (interface_number >= 8 || desc == nullptr || len == 0) return;

    HidMouseDescriptor parsed;
    const bool ok = hid_parse_report_descriptor(desc, len, &parsed);

    std::lock_guard<std::mutex> lk(g_state.layout_mutex);
    InterfaceLayout& il = g_state.iface_layouts[interface_number];
    if (il.ready && il.desc.parsed == parsed.parsed &&
        il.desc.layout_count == parsed.layout_count && il.desc.uses_report_ids == parsed.uses_report_ids) {
        return;  // 同一份描述符重复回调：不重复解析、不重复打日志
    }
    il.ready = true;
    il.desc = parsed;
    il.usable = ok && parsed.usable();

    const HidReportLayout* xy = il.usable ? parsed.xy_layout() : nullptr;
    const HidReportLayout* bt = il.usable ? parsed.button_layout() : nullptr;

    fprintf(stderr,
            "[mouse_control][LAYOUT] 接口 %u 描述符 %u 字节：解析%s，%d 份报告，report_id 前缀=%s\n",
            interface_number, len, il.usable ? "成功" : "失败/无可用字段",
            parsed.layout_count, parsed.uses_report_ids ? "有" : "无");
    // 描述符原文（最多 64 字节）：现场一旦解析不对，这是唯一能把问题复现出来的证据。
    // 之所以要它 —— 详情见文件头：上一轮就是因为"布局靠猜、日志无声"才烧了一整轮排查。
    if (!il.usable) {
        const char* kHex = "0123456789abcdef";
        char hex[3 * 64 + 1];
        int n = 0;
        const uint32_t show = len < 64 ? len : 64;
        for (uint32_t i = 0; i < show; ++i) {
            hex[n++] = kHex[desc[i] >> 4];
            hex[n++] = kHex[desc[i] & 0x0F];
        }
        hex[n] = '\0';
        fprintf(stderr, "[mouse_control][LAYOUT]   描述符原文(%u/%u 字节)：%s\n", show, len, hex);
    }
    if (xy != nullptr) {
        fprintf(stderr,
                "[mouse_control][LAYOUT]   位移报告 rid=%d：X@bit%d/%dbit Y@bit%d/%dbit wheel=%s，"
                "报告共 %d 字节\n",
                xy->report_id, xy->x.bit_offset, xy->x.bit_size,
                xy->y.bit_offset, xy->y.bit_size,
                xy->wheel.present ? "有" : "无", xy->total_bytes());
        fprintf(stderr,
                "[mouse_control][LAYOUT]   安全校验：X/Y 与 buttons %s，与 wheel %s\n",
                (hid_fields_overlap_bytes(xy->x, xy->buttons) ||
                 hid_fields_overlap_bytes(xy->y, xy->buttons)) ? "重叠(将拒写)" : "不重叠",
                (hid_fields_overlap_bytes(xy->x, xy->wheel) ||
                 hid_fields_overlap_bytes(xy->y, xy->wheel)) ? "重叠(将拒写)" : "不重叠");
    }
    if (bt != nullptr) {
        fprintf(stderr, "[mouse_control][LAYOUT]   按键报告 rid=%d：buttons@bit%d/%dbit\n",
                bt->report_id, bt->buttons.bit_offset, bt->buttons.bit_size);
    }
}

bool mouse_control_get_layout(uint8_t interface_number, HidMouseDescriptor* out) {
    if (interface_number >= 8 || out == nullptr) return false;
    std::lock_guard<std::mutex> lk(g_state.layout_mutex);
    const InterfaceLayout& il = g_state.iface_layouts[interface_number];
    if (!il.ready) return false;
    *out = il.desc;
    return true;
}

// 物理 HID 报告到达时：把挂起的 AI 位移合并进**这份报告真实的 X/Y 字段**。
// 布局来自该接口的描述符解析；任何一处对不上就原样返回 ——
// 绝不猜偏移、绝不碰 buttons/wheel 的字节（旧代码正是这么把滚轮写乱、害得游戏疯狂切枪的）。
bool mouse_control_merge_report(uint8_t interface_number, uint8_t* data, uint32_t len) {
    if (!g_state.mouse_control_enabled.load()) return false;
    if (data == nullptr || len == 0) return false;

    const uint8_t* body = nullptr;
    size_t body_len = 0;
    const HidReportLayout* lay = layout_for_report(interface_number, data, len, &body, &body_len);
    if (lay == nullptr) {
        count_reject(kRejectNoLayout);
        return false;
    }
    if (!lay->has_xy()) {
        count_reject(kRejectNoXy);
        return false;
    }
    // 安全门①：X/Y 的位宽与对齐必须可安全读写
    if (!hid_field_is_safe(lay->x) || !hid_field_is_safe(lay->y)) {
        count_reject(kRejectField);
        return false;
    }
    // 安全门②：X/Y 的字节区间不得与 buttons / wheel 重叠。
    // 旧代码写死 X@3 Y@5，对现场鼠标正好压在 wheel 上 ⇒ 每帧一个滚轮事件 ⇒ 疯狂切枪。
    // 这条断言就是那个 bug 的墓碑：只要重叠，宁可自瞄不生效也不写。
    if (hid_fields_overlap_bytes(lay->x, lay->buttons) ||
        hid_fields_overlap_bytes(lay->y, lay->buttons) ||
        hid_fields_overlap_bytes(lay->x, lay->wheel) ||
        hid_fields_overlap_bytes(lay->y, lay->wheel)) {
        count_reject(kRejectField);
        return false;
    }
    // 安全门③：报告必须放得下布局声明的字节数
    if (body_len < static_cast<size_t>(lay->total_bytes())) {
        count_reject(kRejectLen);
        return false;
    }

    const int32_t dx = g_state.pending_dx.exchange(0);
    const int32_t dy = g_state.pending_dy.exchange(0);
    if (dx == 0 && dy == 0) {
        g_layout_diag.merge_ok.fetch_add(1);
        return false;
    }

    int32_t cur_x = 0;
    int32_t cur_y = 0;
    uint8_t* mutable_body = const_cast<uint8_t*>(body);
    if (!hid_field_read_signed(body, body_len, lay->x, &cur_x) ||
        !hid_field_read_signed(body, body_len, lay->y, &cur_y)) {
        count_reject(kRejectLen);
        return false;
    }
    if (!hid_field_write_signed(mutable_body, body_len, lay->x, cur_x + dx) ||
        !hid_field_write_signed(mutable_body, body_len, lay->y, cur_y + dy)) {
        count_reject(kRejectLen);
        return false;
    }
    g_state.merge_count.fetch_add(1);
    g_layout_diag.merge_ok.fetch_add(1);
    g_state.last_move_ts_us.store(now_us());
    return true;
}

// 物理报告解析：按**真实的 buttons 字段**更新按键掩码 + 通知订阅者。
// BUTTON_EVENT payload = <BBBQ button, pressed(1=down/0=up), mask, timestamp_ns
//
// ★ 按键报告与位移报告常常不是同一个 report_id。旧代码硬要 rid==2，又从 `data[1..2]`
//   读 buttons —— 对现场鼠标那就是 X 位移，掩码永远是被污染的垃圾（实测 0xff）。
void mouse_control_notify_physical_report(uint8_t interface_number,
                                          const uint8_t* data, uint32_t len) {
    if (!g_state.mouse_control_enabled.load()) return;
    if (data == nullptr || len == 0) return;

    const uint8_t* body = nullptr;
    size_t body_len = 0;
    const HidReportLayout* lay = layout_for_report(interface_number, data, len, &body, &body_len);
    if (lay == nullptr) return;  // 静默：同一份报告 merge 那条路已在计数，不重复记
    if (!lay->has_buttons()) return;

    uint32_t raw_mask = 0;
    if (!hid_field_read_mask(body, body_len, lay->buttons, &raw_mask)) {
        count_reject(kRejectNoButtons);
        return;
    }
    const uint8_t mask = static_cast<uint8_t>(raw_mask & 0xFF);
    const uint8_t old = g_state.button_mask.exchange(mask);
    const uint8_t changed = static_cast<uint8_t>(old ^ mask);
    if (changed == 0) return;

    const int64_t ts_ns = now_ns();
    const uint32_t rid = 0;
    std::lock_guard<std::mutex> lk(g_state.subscribers_mutex);
    // 每个变化的按钮发一条 BUTTON_EVENT：button=1..5, pressed, mask, ts
    for (int b = 1; b <= 5; b++) {
        const uint8_t bit = static_cast<uint8_t>(1u << (b - 1));
        if (!(changed & bit)) continue;
        uint8_t ev[11] = {0};
        ev[0] = static_cast<uint8_t>(b);
        ev[1] = (mask & bit) ? 1 : 0;
        ev[2] = mask;
        std::memcpy(ev + 3, &ts_ns, 8);
        for (int fd : g_state.subscribers) {
            send_packet(fd, kButtonEvent, rid, ev, sizeof(ev));
        }
    }
}

// synthetic 模式：从挂起位移构造合成 HID 报告
// boot 鼠标布局(与 gadget-config.json 描述符一致, 无 report_id):
// [0]=buttons u8, [1]=X int8, [2]=Y int8, [3]=wheel int8
int mouse_control_build_synthetic_report(uint8_t* out, uint32_t cap) {
    int32_t dx = g_state.pending_dx.exchange(0);
    int32_t dy = g_state.pending_dy.exchange(0);
    int32_t wheel = g_state.pending_wheel.exchange(0);
    if (dx == 0 && dy == 0 && wheel == 0) return 0;
    if (cap < 4) return 0;
    std::memset(out, 0, 4);
    out[0] = g_state.button_mask.load();
    auto put_i8 = [&](int off, int32_t v) {
        if (v < -128) v = -128;
        if (v > 127) v = 127;
        out[off] = static_cast<uint8_t>(static_cast<int8_t>(v));
    };
    put_i8(1, dx);
    put_i8(2, dy);
    put_i8(3, wheel);
    g_state.merge_count.fetch_add(1);
    g_state.last_move_ts_us.store(now_us());
    return 4;
}

}  // namespace ttbox_usbproxy
