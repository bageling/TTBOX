// ttbox-hid-bridge.c — 3 路双向 HID 透传桥 + A10 AI 注入 + TTBox 功能层
//
// 键盘(hidrawX↔hidg0)、鼠标(hidrawY↔hidg1)、HID++(hidrawZ↔hidg2)
// 按 HID_PHYS 匹配（/input0 键盘、/input1 鼠标、/input2 HID++）。
//
// A10 AI 注入：
//   - 读取 /run/ttbox-aim.fifo（test_worker_hw MouseScheduler 写入）
//     * type=0x01 移动帧：dx(int16 LE) + dy(int16 LE)
//     * type=0x02 控制帧：flags + hotkey_mask（bit0=enabled bit1=block_x bit2=block_y）
//   - 热键按住且 enabled → 瞄准注入 final = block ? ai : phys + ai
//
// TTBox 功能层（读取 /run/ttbox-features.conf，python 后端每次保存 profile 时刷新）：
//   - 屏蔽物理按键（blocked_physical_buttons）
//   - 全局热键禁用（hotkey_guard，toggle 键切换）
//   - 压枪（recoil：热键按住 → 垂直补偿，px/s × kp_y → count）
//   - 连点（rapid_fire：热键按住 → 循环左键点击）
//   - 自动开火（auto_trigger：瞄准中且目标接近 → 左键按住）
//   - 自动背闪（auto_back_flick：热键按住 → 定期反向位移）
//   - 开火锁Y（aim_fire_lock_y：开火超过时长 → Y 输出清零）
//   - 拉枪曲线 / 持续提前量（对 AI 输出整形）
//
// 编译: gcc -O2 -o ttbox-hid-forward ttbox-hid-bridge.c
#define _GNU_SOURCE
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <math.h>
#include <poll.h>
#include <signal.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <time.h>
#include <unistd.h>

#define AIM_FIFO "/run/ttbox-aim.fifo"
#define STATS_FILE "/run/ttbox-mouse-stats.json"
#define FEATURES_CONF "/run/ttbox-features.conf"
#define STATS_INTERVAL_MS 100   // 状态写盘间隔（越低 C++ 热键反馈越快；tmpfs 开销可忽略）

static long long now_ms(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC, &ts);
    return (long long)ts.tv_sec * 1000 + (long long)(ts.tv_nsec / 1000000);
}

static volatile int g_stop = 0;
static void on_sig(int) { g_stop = 1; }

// ---- AI 注入全局状态 ----
static int g_fifo_fd = -1;
static int16_t g_ai_dx = 0, g_ai_dy = 0;
static int g_ai_pending = 0;        // 有未应用的 AI 增量（每条 AI 帧只应用一次）
static long long g_ai_last_ms = 0;  // 最后一条 AI 帧时间（陈旧增量忽略，防调度器停止后残留）
static uint8_t g_ctrl_flags = 0;    // bit0=enabled bit1=block_x bit2=block_y
static uint8_t g_ctrl_hotkey = 0x02;  // 默认右键

// ---- 统计 ----
static volatile uint32_t g_hid_tx = 0;
static volatile int g_aiming = 0;
static volatile int16_t g_phys_dx = 0, g_phys_dy = 0;
static volatile int16_t g_final_dx = 0, g_final_dy = 0;

// ---- 功能配置（/run/ttbox-features.conf） ----
struct Feat {
    int hotkey_guard_enabled;
    unsigned hotkey_guard_toggle;
    int guard_toggle_scroll;             // toggle 为键盘 Scroll Lock（键码 0x47）
    int guard_on;                        // 运行时 toggle 状态
    unsigned blocked_mask;              // 屏蔽物理按键位
    int pull_curve_enabled;
    float pull_curve_strength, pull_curve_jitter_px, pull_curve_min_distance;
    int continuous_lead_enabled;
    float lead_enter, lead_scale, lead_fade_in, lead_fade_out, lead_near_ratio;
    int fire_lock_y;
    unsigned fire_hotkey_bit;
    float fire_release_sec;
    int recoil_enabled;
    unsigned recoil_hotkey;
    float recoil_strength, recoil_speed;
    int recoil_only_target;
    float recoil_release_ms;
    int recoil_delay_enabled;
    float recoil_delay_ms;
    int recoil_humanize;
    float recoil_curve, recoil_jitter, recoil_jfreq;
    int rapid_enabled;
    unsigned rapid_hotkey;
    float rapid_press, rapid_interval;
    int auto_trigger_enabled;
    unsigned at_hotkey;
    int back_flick_enabled;
    unsigned back_flick_hotkey;         // 背闪触发键（默认 left=1）
    float kp_x, kp_y;
    int mouse_enabled;
    int calibrating;               // 标定模式：AI 移动帧无条件注入（不依赖热键/物理静止）
} g_feat;
static long long g_conf_mtime = 0;

static unsigned hk_bit(const char* name) {
    if (!name) return 0;
    if (strcmp(name, "left") == 0) return 1;
    if (strcmp(name, "right") == 0) return 2;
    if (strcmp(name, "middle") == 0) return 4;
    if (strcmp(name, "back") == 0) return 8;
    if (strcmp(name, "forward") == 0) return 16;
    return 0;
}

static void parse_feature(const char* line) {
    char key[64] = {0}, val[96] = {0};
    if (sscanf(line, "%63[^=]=%95s", key, val) != 2) return;
    const float fv = atof(val);
    const int iv = atoi(val);
    struct Feat* F = &g_feat;
    if (strcmp(key, "hotkey_guard.enabled") == 0) F->hotkey_guard_enabled = iv;
    else if (strcmp(key, "hotkey_guard.toggle_hotkey") == 0) {
        if (strcmp(val, "scroll_lock") == 0) { F->guard_toggle_scroll = 1; F->hotkey_guard_toggle = 0; }
        else { F->guard_toggle_scroll = 0; F->hotkey_guard_toggle = hk_bit(val); }
    }
    else if (strcmp(key, "mouse_output.blocked_physical_buttons") == 0) {
        // 逗号分隔的按键名列表
        unsigned mask = 0;
        char tmp[96]; strncpy(tmp, val, sizeof(tmp) - 1); tmp[sizeof(tmp) - 1] = 0;
        char* tok = strtok(tmp, ",");
        while (tok) { mask |= hk_bit(tok); tok = strtok(NULL, ","); }
        F->blocked_mask = mask;
    }
    else if (strcmp(key, "ai_controller.pull_curve_enabled") == 0) F->pull_curve_enabled = iv;
    else if (strcmp(key, "ai_controller.pull_curve_strength") == 0) F->pull_curve_strength = fv;
    else if (strcmp(key, "ai_controller.pull_curve_jitter_px") == 0) F->pull_curve_jitter_px = fv;
    else if (strcmp(key, "ai_controller.pull_curve_min_distance") == 0) F->pull_curve_min_distance = fv;
    else if (strcmp(key, "ai_controller.continuous_lead_enabled") == 0) F->continuous_lead_enabled = iv;
    else if (strcmp(key, "ai_controller.continuous_lead_enter_distance") == 0) F->lead_enter = fv;
    else if (strcmp(key, "ai_controller.continuous_lead_scale") == 0) F->lead_scale = fv;
    else if (strcmp(key, "ai_controller.continuous_lead_fade_in_ms") == 0) F->lead_fade_in = fv;
    else if (strcmp(key, "ai_controller.continuous_lead_fade_out_ms") == 0) F->lead_fade_out = fv;
    else if (strcmp(key, "ai_controller.continuous_lead_near_disable_ratio") == 0) F->lead_near_ratio = fv;
    else if (strcmp(key, "ai_controller.aim_fire_lock_y") == 0) F->fire_lock_y = iv;
    else if (strcmp(key, "ai_controller.y_axis_fire_hotkey") == 0) F->fire_hotkey_bit = hk_bit(val);
    else if (strcmp(key, "ai_controller.y_axis_fire_release_delay_sec") == 0) F->fire_release_sec = fv;
    else if (strcmp(key, "ai_controller.block_physical_mouse_x_while_aiming") == 0) g_ctrl_flags = iv ? (g_ctrl_flags | 0x02) : (g_ctrl_flags & ~0x02);
    else if (strcmp(key, "ai_controller.block_physical_mouse_y_while_aiming") == 0) g_ctrl_flags = iv ? (g_ctrl_flags | 0x04) : (g_ctrl_flags & ~0x04);
    else if (strcmp(key, "recoil.enabled") == 0) F->recoil_enabled = iv;
    else if (strcmp(key, "recoil.hotkey") == 0) F->recoil_hotkey = hk_bit(val);
    else if (strcmp(key, "recoil.strength") == 0) F->recoil_strength = fv;
    else if (strcmp(key, "recoil.speed") == 0) F->recoil_speed = fv > 0 ? fv : 1;
    else if (strcmp(key, "recoil.only_when_target_visible") == 0) F->recoil_only_target = iv;
    else if (strcmp(key, "recoil.target_lost_release_ms") == 0) F->recoil_release_ms = fv;
    else if (strcmp(key, "recoil.trigger_delay_enabled") == 0) F->recoil_delay_enabled = iv;
    else if (strcmp(key, "recoil.trigger_delay_ms") == 0) F->recoil_delay_ms = fv;
    else if (strcmp(key, "recoil.humanize_enabled") == 0) F->recoil_humanize = iv;
    else if (strcmp(key, "recoil.humanize_curve_strength") == 0) F->recoil_curve = fv;
    else if (strcmp(key, "recoil.humanize_jitter_px") == 0) F->recoil_jitter = fv;
    else if (strcmp(key, "recoil.humanize_jitter_frequency") == 0) F->recoil_jfreq = fv;
    else if (strcmp(key, "rapid_fire.enabled") == 0) F->rapid_enabled = iv;
    else if (strcmp(key, "rapid_fire.hotkey") == 0) F->rapid_hotkey = hk_bit(val);
    else if (strcmp(key, "rapid_fire.press_base_ms") == 0) F->rapid_press = fv;
    else if (strcmp(key, "rapid_fire.interval_base_ms") == 0) F->rapid_interval = fv;
    else if (strcmp(key, "auto_trigger.enabled") == 0) {
        // 明确开关（python 后端按任一 profile.enabled 汇总）；profiles 列表字段忽略
        F->auto_trigger_enabled = iv;
        if (F->at_hotkey == 0) F->at_hotkey = 1;
    }
    else if (strcmp(key, "auto_back_flick.enabled") == 0) F->back_flick_enabled = iv;
    else if (strcmp(key, "crosshair.detection_enabled") == 0) { /* 准星找色由后端 python 实现 */ }
    else if (strcmp(key, "mouse.kp_x") == 0) F->kp_x = fv;
    else if (strcmp(key, "mouse.kp_y") == 0) F->kp_y = fv;
    else if (strcmp(key, "mouse.enabled") == 0) F->mouse_enabled = iv;
    else if (strcmp(key, "mouse.calibrating") == 0) F->calibrating = iv;
}

static void load_features(void) {
    struct stat st;
    if (stat(FEATURES_CONF, &st) != 0) return;
    if (st.st_mtime == g_conf_mtime) return;
    g_conf_mtime = st.st_mtime;
    memset(&g_feat, 0, sizeof(g_feat));
    g_feat.kp_x = 17; g_feat.kp_y = 10; g_feat.recoil_speed = 1; g_feat.at_hotkey = 1;
    g_feat.back_flick_hotkey = 1;
    FILE* f = fopen(FEATURES_CONF, "r");
    if (!f) return;
    char line[256];
    while (fgets(line, sizeof(line), f)) parse_feature(line);
    fclose(f);
}

// ---- hidg 写（全局鼠标端点，供功能层注入） ----
static int g_ms_hidg = -1;
static unsigned char g_last_mouse[9] = {0};
static int g_last_mouse_len = 0;
static unsigned g_last_buttons = 0;
static long long g_last_ms_report_ms = 0;

static void write_all(int fd, const unsigned char* buf, size_t n);

static void send_mouse_raw(const unsigned char* r, size_t n) {
    if (g_ms_hidg < 0) return;
    write_all(g_ms_hidg, r, n);
    g_hid_tx++;
}

// 构造并发送合成鼠标报告（保持 ReportID/布局，buttons 替换）
static void send_synth(unsigned buttons) {
    unsigned char r[9];
    memcpy(r, g_last_mouse, sizeof(r));
    if (g_last_mouse_len == 0) { r[0] = 0x02; }
    r[1] = (unsigned char)(buttons & 0xFF);
    r[2] = (unsigned char)((buttons >> 8) & 0xFF);
    r[3] = 0; r[4] = 0; r[5] = 0; r[6] = 0;
    send_mouse_raw(r, g_last_mouse_len ? (size_t)g_last_mouse_len : 9);
}

static void send_move(int dx, int dy) {
    unsigned char r[9];
    memcpy(r, g_last_mouse, sizeof(r));
    if (g_last_mouse_len == 0) r[0] = 0x02;
    const int16_t cx = (int16_t)dx, cy = (int16_t)dy;
    r[3] = (unsigned char)(cx & 0xFF); r[4] = (unsigned char)((cx >> 8) & 0xFF);
    r[5] = (unsigned char)(cy & 0xFF); r[6] = (unsigned char)((cy >> 8) & 0xFF);
    send_mouse_raw(r, g_last_mouse_len ? (size_t)g_last_mouse_len : 9);
}

// ---- 功能状态机 ----
static int g_rapid_on = 0, g_rapid_phase = 0;
static long long g_rapid_t = 0;
static int g_at_fire = 0, g_at_ready = 0;
static long long g_fire_hold_start = 0;
static int g_fire_held = 0;
static long long g_recoil_start = 0;
static long long g_backflick_t = 0;
static long long g_lead_last = 0;
static int32_t g_lead_accum = 0;      // 持续提前量累计（同向）
static int g_lead_dir = 0;
static float g_lead_level = 0.0f;

static void update_rapid(long long now, int hotkey_on) {
    if (!g_feat.rapid_enabled || !hotkey_on) {
        if (g_rapid_on) { send_synth(g_last_buttons & ~1u); g_rapid_on = 0; }
        return;
    }
    if (!g_rapid_on) { g_rapid_on = 1; g_rapid_phase = 1; g_rapid_t = now; send_synth(g_last_buttons | 1u); return; }
    const long long el = now - g_rapid_t;
    const float press = g_feat.rapid_press > 1 ? g_feat.rapid_press : 30;
    const float interval = g_feat.rapid_interval > 1 ? g_feat.rapid_interval : 60;
    if (g_rapid_phase == 1 && el >= (long long)press) { g_rapid_phase = 0; g_rapid_t = now; send_synth(g_last_buttons & ~1u); }
    else if (g_rapid_phase == 0 && el >= (long long)interval) { g_rapid_phase = 1; g_rapid_t = now; send_synth(g_last_buttons | 1u); }
}

static void update_auto_fire(long long now, int hotkey_on) {
    int aiming = g_aiming;
    if (!g_feat.auto_trigger_enabled) { if (g_at_fire) { send_synth(g_last_buttons & ~1u); g_at_fire = 0; } g_at_ready = 0; return; }
    if (!aiming || !hotkey_on) {
        if (g_at_fire) { send_synth(g_last_buttons & ~1u); g_at_fire = 0; }
        g_at_ready = 0;
        return;
    }
    const int close = abs(g_ai_dx) < 4 && abs(g_ai_dy) < 4;
    if (close) {
        if (!g_at_ready) g_at_ready = 1;
        else if (!g_at_fire) { g_at_fire = 1; send_synth(g_last_buttons | 1u); }
    } else {
        if (g_at_fire) { send_synth(g_last_buttons & ~1u); g_at_fire = 0; }
        g_at_ready = 0;
    }
}

static void update_back_flick(long long now, int hotkey_on) {
    if (!g_feat.back_flick_enabled || !hotkey_on) { g_backflick_t = 0; return; }
    if (now - g_backflick_t >= 180) { g_backflick_t = now; send_move(-25, 0); }
}

// 持续提前量：AI 输出同向累计超阈值 → 加 X 偏置（渐入渐出）
static float continuous_lead_output(long long now, int16_t ai_dx, int16_t ai_dy) {
    if (!g_feat.continuous_lead_enabled || g_feat.lead_scale <= 0) { g_lead_level = 0; return 0; }
    if (ai_dx == 0 && ai_dy == 0) { if (now - g_lead_last > 300) { g_lead_accum = 0; g_lead_dir = 0; } return 0; }
    g_lead_last = now;
    const int dir = ai_dx >= 0 ? 1 : -1;
    if (g_lead_dir != 0 && g_lead_dir != dir) { g_lead_accum = 0; }
    g_lead_dir = dir;
    g_lead_accum += abs(ai_dx);
    if (g_lead_accum < g_feat.lead_enter) return 0;
    const float target = g_feat.lead_scale * (float)abs(ai_dx) * 0.5f;
    const float step = g_feat.lead_fade_in > 0 ? (float)(now - (now - g_lead_accum)) : 0;
    g_lead_level += (target - g_lead_level) * 0.2f;
    if (g_lead_level > target) g_lead_level = target;
    return g_lead_level * (float)dir;
}

// ---- 鼠标报告解析 ----
static int parse_mouse(const unsigned char* b, size_t n, uint16_t* buttons,
                       int16_t* dx, int16_t* dy) {
    if (n < 7) return 0;
    if (b[0] != 0x02) return 0;
    *buttons = (uint16_t)(b[1] | ((uint16_t)b[2] << 8));
    *dx = (int16_t)(b[3] | ((uint16_t)b[4] << 8));
    *dy = (int16_t)(b[5] | ((uint16_t)b[6] << 8));
    return 1;
}

static int16_t clamp16(int32_t v) {
    if (v > 32767) return 32767;
    if (v < -32768) return -32768;
    return (int16_t)v;
}

// 实时热键判定（基于最近物理鼠标报告）。
// 必须周期刷新：inject_ai 仅在收到物理报告时更新 g_aiming，鼠标静止时
// 松开按键后 g_aiming 会卡在旧值 → stats 一直报 aiming:1 → 未按键仍瞄准。
static int compute_aiming(void) { if (g_feat.calibrating) return 1;
    const int enabled = (g_ctrl_flags & 0x01) != 0;
    if (!enabled) return 0;
    const int all_mode = (g_ctrl_flags & 0x08) != 0;
    const int hotkey_on = all_mode ? ((g_last_buttons & g_ctrl_hotkey) == g_ctrl_hotkey)
                                   : ((g_last_buttons & g_ctrl_hotkey) != 0);
    return hotkey_on;
}

// 瞄准注入：修改鼠标报告 dx/dy 字节（保持 int16 LE），返回是否修改
static int inject_ai(unsigned char* b, size_t n, uint16_t buttons, int16_t phys_dx, int16_t phys_dy, long long now) {
    const int block_x = (g_ctrl_flags & 0x02) != 0;
    const int block_y = (g_ctrl_flags & 0x04) != 0;
    g_aiming = compute_aiming();
    if (!g_aiming) return 0;
    int32_t fx = (block_x ? 0 : phys_dx);
    int32_t fy = (block_y ? 0 : phys_dy);
    // 合并路径：仅在"本 AI 增量尚未被应用"时叠加一次（用后清零）。
    // 旧实现把同一 g_ai_dx 加到每一条物理报告上（500~1000Hz），而 AI 帧只有
    // 250Hz → 同一增量被重复放大 2~4 倍（鼠标活动时 AI 强度虚高）。
    // 陈旧保护：调度器停止写帧（>100ms 无新帧）后忽略残留增量，防漂移。
    const int ai_fresh = (g_ai_pending && (now - g_ai_last_ms) < 100);
    if (ai_fresh) {
        fx += g_ai_dx;
        fy += g_ai_dy;
        g_ai_pending = 0;   // 已消费：本帧增量只应用一次
    }
    // 开火锁Y：开火按住超过 release_delay → Y 输出清零
    if (g_feat.fire_lock_y && g_feat.fire_hotkey_bit && (buttons & g_feat.fire_hotkey_bit)) {
        if (!g_fire_held) { g_fire_held = 1; g_fire_hold_start = now; }
        if (g_feat.fire_release_sec > 0 && (now - g_fire_hold_start) >= (long long)(g_feat.fire_release_sec * 1000)) fy = 0;
    } else if (g_fire_held) { g_fire_held = 0; }
    // 拉枪曲线：已迁移到 C++ 端（MouseScheduler 误差域 px 注入 + 新目标门控），
    // 此处禁用旧 count 域实现（原 abs(dx) 判定单位错 + 无门控 + 注入位置错）。
    // if (g_feat.pull_curve_enabled && abs(g_ai_dx) >= (int)g_feat.pull_curve_min_distance) {
    //     float arc = g_feat.pull_curve_strength * (float)abs(g_ai_dx) * 0.08f;
    //     if (arc > 24) arc = 24;
    //     fy += (int32_t)(g_ai_dx >= 0 ? arc : -arc);
    //     if (g_feat.pull_curve_jitter_px > 0) fy += (int32_t)(((rand() % 200) / 100.0f - 1.0f) * g_feat.pull_curve_jitter_px);
    // }
    // 持续提前量（X 偏置）：只在"新鲜 AI 帧"到达时累计/输出一次（与 AI 帧同频）。
    // 旧实现按物理报告频率累计（同一增量累计多次）→ 提前量提前 ~报告率/AI率 倍触发。
    if (ai_fresh) fx += (int32_t)continuous_lead_output(now, g_ai_dx, g_ai_dy);
    const int16_t fdx = clamp16(fx);
    const int16_t fdy = clamp16(fy);
    b[3] = (unsigned char)(fdx & 0xFF);
    b[4] = (unsigned char)((fdx >> 8) & 0xFF);
    b[5] = (unsigned char)(fdy & 0xFF);
    b[6] = (unsigned char)((fdy >> 8) & 0xFF);
    g_final_dx = fdx;
    g_final_dy = fdy;
    return 1;
}

// ---- 统计写盘（每 500ms）----
static void write_stats(void) {
    g_aiming = compute_aiming();  // 实时按键状态（不依赖物理报告到达）
    char buf[320];
    const time_t t = time(NULL);
    snprintf(buf, sizeof(buf),
             "{\"ts\":%ld,\"aiming\":%d,\"enabled\":%d,\"hotkey\":%u,\"buttons\":%u,"
             "\"phys_dx\":%d,\"phys_dy\":%d,\"ai_dx\":%d,\"ai_dy\":%d,"
             "\"final_dx\":%d,\"final_dy\":%d,\"hid_tx\":%u,"
             "\"guard\":%d,\"recoil\":%d,\"rapid\":%d,\"auto_fire\":%d}\n",
             (long)t, (int)g_aiming, (g_ctrl_flags & 0x01) ? 1 : 0, g_ctrl_hotkey, g_last_buttons,
             (int)g_phys_dx, (int)g_phys_dy, (int)g_ai_dx, (int)g_ai_dy,
             (int)g_final_dx, (int)g_final_dy, (unsigned)g_hid_tx,
             g_feat.guard_on, g_feat.recoil_enabled, g_rapid_on, g_at_fire);
    int fd = open(STATS_FILE, O_WRONLY | O_CREAT | O_TRUNC, 0644);
    if (fd >= 0) {
        (void)!write(fd, buf, strlen(buf));
        close(fd);
    }
}

// ---- 按 HID_PHYS 后缀匹配 hidraw ----
static int find_hidraw(const char* suffix, char* out, size_t outsz) {
    DIR* d = opendir("/sys/class/hidraw");
    if (!d) return -1;
    struct dirent* e;
    int rc = -1;
    while ((e = readdir(d)) != NULL) {
        if (strncmp(e->d_name, "hidraw", 6) != 0) continue;
        char path[256], uev[512];
        snprintf(path, sizeof(path), "/sys/class/hidraw/%s/device/uevent", e->d_name);
        FILE* f = fopen(path, "r");
        if (!f) continue;
        while (fgets(uev, sizeof(uev), f)) {
            if (strncmp(uev, "HID_PHYS=", 9) == 0) {
                uev[strcspn(uev, "\n")] = 0;
                size_t len = strlen(uev), sl = strlen(suffix);
                if (len >= sl && strcmp(uev + len - sl, suffix) == 0) {
                    snprintf(out, outsz, "/dev/%s", e->d_name);
                    rc = 0;
                }
                break;
            }
        }
        fclose(f);
        if (rc == 0) break;
    }
    closedir(d);
    return rc;
}

static int open_nb(const char* path, int flags) {
    return open(path, flags | O_NONBLOCK);
}

static void write_all(int fd, const unsigned char* buf, size_t n) {
    size_t off = 0;
    while (off < n) {
        ssize_t w = write(fd, buf + off, n - off);
        if (w < 0) {
            if (errno == EAGAIN || errno == EWOULDBLOCK || errno == ENODEV) break;
            if (errno == EINTR) continue;
            break;
        }
        off += (size_t)w;
    }
}

// ---- AI FIFO 解析 ----
static unsigned char g_fifo_buf[16];
static size_t g_fifo_len = 0;
static int g_sl_prev = 0;   // 键盘 Scroll Lock 状态（guard toggle 用，边沿检测）

static void handle_ai_frame(const unsigned char* f, size_t n) {
    if (n < 1) return;
    if (f[0] == 0x01 && n >= 5) {
        g_ai_dx = (int16_t)(f[1] | ((uint16_t)f[2] << 8));
        g_ai_dy = (int16_t)(f[3] | ((uint16_t)f[4] << 8));
        g_ai_pending = 1;               // 新 AI 增量待应用（每条只应用一次）
        g_ai_last_ms = now_ms();
        // 主动发送：AI enabled 且非零移动，且热键有效，且物理鼠标 15ms 无报告
        // （静止/标定）→ 直接注入；否则 AI 叠加在物理报告上（inject_ai）。
        // 标定模式（mouse.calibrating=1）：无条件注入，不依赖热键/物理静止。
        // 必须加 compute_aiming() 热键门控：C++ 端热键状态有缓存滞后，松开热键
        // 后其状态机仍可能短暂输出 AI 帧，若此处不检查热键 → 幽灵瞄准。
        const int calib_force = g_feat.calibrating;
        const int aiming_ok = calib_force || compute_aiming();
        if ((g_ctrl_flags & 0x01) && (g_ai_dx != 0 || g_ai_dy != 0) &&
            aiming_ok && (calib_force || (now_ms() - g_last_ms_report_ms) > 15)) {
            send_move(g_ai_dx, g_ai_dy);
            g_ai_pending = 0;           // 直发路径已消费本帧增量（防与合并路径双发）
        }
    } else if (f[0] == 0x02 && n >= 6) {
        g_ctrl_flags = f[1];
        g_ctrl_hotkey = f[2];
    }
}

static void drain_fifo(void) {
    if (g_fifo_fd < 0) return;
    unsigned char tmp[64];
    ssize_t r = read(g_fifo_fd, tmp, sizeof(tmp));
    while (r > 0) {
        size_t i = 0;
        while (i < (size_t)r) {
            size_t avail = g_fifo_len;
            while (avail < 6 && i < (size_t)r) g_fifo_buf[avail++] = tmp[i++];
            if (avail < 1) break;
            size_t need = (g_fifo_buf[0] == 0x02) ? 6 : 5;
            if (avail < need) {
                g_fifo_len = avail;
                break;
            }
            handle_ai_frame(g_fifo_buf, need);
            size_t rest = avail - need;
            if (rest > 0) memmove(g_fifo_buf, g_fifo_buf + need, rest);
            g_fifo_len = rest;
        }
        r = read(g_fifo_fd, tmp, sizeof(tmp));
    }
}

int main(void) {
    char kb[32], ms[32], hp[32];
    if (find_hidraw("/input0", kb, sizeof(kb)) < 0) { fprintf(stderr, "no keyboard hidraw\n"); return 1; }
    if (find_hidraw("/input1", ms, sizeof(ms)) < 0) { fprintf(stderr, "no mouse hidraw\n"); return 1; }
    if (find_hidraw("/input2", hp, sizeof(hp)) < 0) { fprintf(stderr, "no hidpp hidraw\n"); return 1; }

    struct { int a, b; } p[3];
    p[0].a = open_nb(kb, O_RDWR);
    p[0].b = open_nb("/dev/hidg0", O_RDWR);
    p[1].a = open_nb(ms, O_RDWR);
    p[1].b = open_nb("/dev/hidg1", O_RDWR);
    p[2].a = open_nb(hp, O_RDWR);
    p[2].b = open_nb("/dev/hidg2", O_RDWR);
    for (int i = 0; i < 3; i++)
        if (p[i].a < 0 || p[i].b < 0) {
            fprintf(stderr, "open fail pair %d: %s\n", i, strerror(errno));
            return 1;
        }
    g_ms_hidg = p[1].b;

    unsigned char zkb[8] = {0}, zms[9] = {0};
    write_all(p[0].b, zkb, 8);
    write_all(p[1].b, zms, 9);

    mkfifo(AIM_FIFO, 0666);
    g_fifo_fd = open_nb(AIM_FIFO, O_RDWR);

    signal(SIGINT, on_sig);
    signal(SIGTERM, on_sig);

    struct pollfd fds[7];
    int pair_of_fd[6] = {0, 0, 1, 1, 2, 2};
    fds[0].fd = p[0].a; fds[0].events = POLLIN;
    fds[1].fd = p[0].b; fds[1].events = POLLIN;
    fds[2].fd = p[1].a; fds[2].events = POLLIN;
    fds[3].fd = p[1].b; fds[3].events = POLLIN;
    fds[4].fd = p[2].a; fds[4].events = POLLIN;
    fds[5].fd = p[2].b; fds[5].events = POLLIN;
    fds[6].fd = g_fifo_fd; fds[6].events = POLLIN;

    unsigned char buf[64];
    printf("ttbox-hid-bridge: kb=%s ms=%s hidpp=%s running\n", kb, ms, hp);
    fflush(stdout);

    long long last_stats_ms = 0, last_feat_ms = 0;
    int guard_prev = 0;
    while (!g_stop) {
        int r = poll(fds, 7, 4);  // 4ms: 无事件时也能以~250Hz推进AI注入
        if (r < 0) { if (errno == EINTR) continue; break; }
        const long long now = now_ms();
        // 周期刷新功能配置
        if (now - last_feat_ms >= 50) { load_features(); last_feat_ms = now; }  // 50ms
        if (g_fifo_fd >= 0 && (fds[6].revents & POLLIN)) drain_fifo();

        // 全局热键禁用：toggle 键切换（物理报告上检测）
        unsigned buttons_now = g_last_buttons;
        if (g_feat.hotkey_guard_enabled && g_feat.hotkey_guard_toggle) {
            if ((buttons_now & g_feat.hotkey_guard_toggle) && !guard_prev) {
                g_feat.guard_on = !g_feat.guard_on;
                if (g_feat.guard_on) { send_synth(g_last_buttons & ~1u); g_at_fire = 0; g_rapid_on = 0; }
            }
            guard_prev = (buttons_now & g_feat.hotkey_guard_toggle) != 0;
        }

        const int guard = g_feat.guard_on;
        // 功能热键是否有效（guard 开启时全部禁用）
        int hk_recoil = !guard && g_feat.recoil_enabled && (buttons_now & g_feat.recoil_hotkey);
        int hk_rapid = !guard && g_feat.rapid_enabled && (buttons_now & g_feat.rapid_hotkey);
        int hk_at = !guard && g_feat.auto_trigger_enabled && (buttons_now & g_feat.at_hotkey);
        int hk_bf = !guard && g_feat.back_flick_enabled && (buttons_now & g_feat.back_flick_hotkey);

        if (r == 0) {
            // 无事件周期：正在瞄准且有新鲜AI增量 -> 合成鼠标报告注入
            if (g_ai_pending && (now - g_ai_last_ms) < 100) {
                g_aiming = compute_aiming();
                if (g_aiming) {
                    unsigned char syn[9] = {0};
                    syn[0] = 0x02;
                    syn[1] = (unsigned char)(g_last_buttons & 0xFF);
                    syn[2] = (unsigned char)((g_last_buttons >> 8) & 0xFF);
                    if (inject_ai(syn, 9, g_last_buttons, 0, 0, now)) {
                        write_all(g_ms_hidg, syn, 9);
                        g_hid_tx++;
                    }
                }
            }
            update_rapid(now, hk_rapid);
            update_auto_fire(now, hk_at);
            update_back_flick(now, hk_bf);
            if (now - last_stats_ms >= STATS_INTERVAL_MS) { write_stats(); last_stats_ms = now; }
            continue;
        }
        for (int i = 0; i < 6; i++) {
            if (!(fds[i].revents & POLLIN)) continue;
            ssize_t n = read(fds[i].fd, buf, sizeof(buf));
            if (n <= 0) continue;
            int pair = pair_of_fd[i];
            int peer = (i % 2 == 0) ? p[pair].b : p[pair].a;
            if (i == 0) {
                // 键盘报告：检测 Scroll Lock（0x47）上升沿 → 切换全局热键禁用
                if ((int)n >= 8 && g_feat.hotkey_guard_enabled && g_feat.guard_toggle_scroll) {
                    int has_sl = 0;
                    for (int k = 2; k < 8; k++) if (buf[k] == 0x47) { has_sl = 1; break; }
                    if (has_sl != g_sl_prev) {
                        g_sl_prev = has_sl;
                        if (has_sl) {
                            g_feat.guard_on = !g_feat.guard_on;
                            if (g_feat.guard_on) { send_synth(g_last_buttons & ~1u); g_at_fire = 0; g_rapid_on = 0; }
                        }
                    }
                }
            }
            if (i == 2) {
                uint16_t buttons = 0;
                int16_t pdx = 0, pdy = 0;
                if (parse_mouse(buf, (size_t)n, &buttons, &pdx, &pdy)) {
                    const long long prev_report_ms = g_last_ms_report_ms;
                    // 保存最新报告（功能层合成点击/移动用）
                    memcpy(g_last_mouse, buf, n < (ssize_t)sizeof(g_last_mouse) ? (size_t)n : sizeof(g_last_mouse));
                    g_last_mouse_len = (int)n;
                    g_last_buttons = buttons;
                    g_last_ms_report_ms = now;
                    g_phys_dx = pdx;
                    g_phys_dy = pdy;
                    // 1) 屏蔽物理按键位
                    if (g_feat.blocked_mask) {
                        buttons &= ~g_feat.blocked_mask;
                        buf[1] = (unsigned char)(buttons & 0xFF);
                        buf[2] = (unsigned char)((buttons >> 8) & 0xFF);
                    }
                    // 2) AI 注入（含曲线/提前量/开火锁Y）
                    inject_ai(buf, (size_t)n, buttons, pdx, pdy, now);
                    // 3) 压枪：热键按住时垂直补偿（px/s × kp → count）
                    if (hk_recoil) {
                        if (g_recoil_start == 0) g_recoil_start = now;
                        const long long hold = now - g_recoil_start;
                        if (!g_feat.recoil_delay_enabled || hold >= (long long)g_feat.recoil_delay_ms) {
                            long long dt = now - prev_report_ms;
                            if (dt <= 0) dt = 1;
                            if (dt > 100) dt = 100;
                            float px = g_feat.recoil_strength * (dt / 1000.0f) * g_feat.recoil_speed;
                            int32_t dy = (int32_t)lround(px * g_feat.kp_y);
                            int32_t fy = (int16_t)(buf[5] | ((uint16_t)buf[6] << 8)) + dy;
                            const int16_t c = clamp16(fy);
                            buf[5] = (unsigned char)(c & 0xFF);
                            buf[6] = (unsigned char)((c >> 8) & 0xFF);
                        }
                    } else { g_recoil_start = 0; }
                }
            }
            write_all(peer, buf, (size_t)n);
            g_hid_tx++;
            (void)pair;
        }
        // 周期功能状态机（连点等也可在事件循环中推进）
        update_rapid(now, hk_rapid);
        update_auto_fire(now, hk_at);
        update_back_flick(now, hk_bf);
        if (now - last_stats_ms >= STATS_INTERVAL_MS) { write_stats(); last_stats_ms = now; }
    }

    if (g_fifo_fd >= 0) close(g_fifo_fd);
    for (int i = 0; i < 3; i++) { close(p[i].a); close(p[i].b); }
    printf("ttbox-hid-bridge stopped\n");
    return 0;
}
