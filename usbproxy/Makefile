# Makefile — TTBOX usbproxy（鼠标注入代理）
#
# 本 Makefile 是 usbproxy 的【唯一】编译入口（README 第三节的 `make` 即指它）。
# 依赖（板端 apt / 交叉编译 sysroot 均需提供）：
#   · libusb-1.0  → <libusb-1.0/libusb.h>、-lusb-1.0
#   · lua          → <lua.h>/<lauxlib.h>/<lualib.h>、-llua5.4
#   · jsoncpp      → <jsoncpp/json/json.h>、-ljsoncpp
#
# 用法：
#   # 板端原生编译（默认）
#   make
#   # 交叉编译（host 侧，指向 aarch64 工具链）
#   make CXX=aarch64-linux-gnu-g++
#   # 产出校验和文件（随包交付，见 DEP-04④）
#   make checksum
#
# 说明（T1.06/DEP-04）：白名单已写入 .gitignore（`!usbproxy/Makefile`）——
# 否则顶层 `**/Makefile` 规则会静默吞掉本文件，clean clone 里没有编译入口。

# ---- 工具链（可覆盖）-----------------------------------------------------------
CXX      ?= c++
AR       ?= ar
STRIP    ?= strip
SHA256SUM?= sha256sum

# ---- 目标与产物 ----------------------------------------------------------------
TARGET    := usb-proxy
# 编译产物落在脚本所在目录（= release 树内 usbproxy/），供 board/run-ttbox-usb-proxy.sh
# 以绝对路径直接 exec。
PREFIX    ?=
DESTDIR   ?=

# ---- 源文件（显式清单，不用通配——避免误收 .bak/临时文件）----------------------
SRCS := \
	usb-proxy.cpp \
	proxy.cpp \
	mouse_control.cpp \
	synthetic.cpp \
	device-libusb.cpp \
	host-raw-gadget.cpp \
	misc.cpp
OBJS := $(SRCS:.cpp=.o)

# ---- 头文件搜索路径（可覆盖）---------------------------------------------------
# libusb / jsoncpp 的头按 <libusb-1.0/...> / <jsoncpp/json/...> 形式包含，
# 走默认 /usr/include 即可；lua 的头在 <lua5.4/…> 之外、按 <lua.h> 形式包含，
# 必须显式把 lua 的 include 目录加进来。
LUA_INCDIR ?= /usr/include/lua5.4

# ---- 编译 / 链接参数 -----------------------------------------------------------
CXXSTD    ?= -std=c++17
# ★ 硬门禁（item 6）：`OPT` 是**外部可覆盖**变量，**不得**含 `-DNDEBUG`。
#   usbproxy 的断言（synthetic.cpp / proxy.cpp 的防御性检查）是**运行期安全网**；一旦
#   `-DNDEBUG` 把它们编译掉，非法/越界输入会被静默放过 —— 即"绿灯下的失效"。
#   Release 交付靠 `-O2`/`-O3` 优化即可，**绝不**用 `-DNDEBUG` 抹断言。
#   纯注释拦不住 `make OPT='-O3 -DNDEBUG'`，故落成 `$(error)` **真门禁** —— 与测试 TU 的
#   `#error` 绊线同源：把"文字承诺"换成"机制"，让违规**构造期即失败**（make -n 也会触发）。
OPT       ?= -O2
ifneq ($(findstring -DNDEBUG,$(OPT)),)
$(error OPT 不得包含 -DNDEBUG（断言被抹除=失效静默）。用 -O2/-O3 即可；当前 OPT=$(OPT))
endif
WARN      ?= -Wall -Wextra
CPPFLAGS  += -I$(LUA_INCDIR)
CXXFLAGS  += $(CXXSTD) $(OPT) $(WARN) -pthread
LDFLAGS   += -pthread
LDLIBS    += -lusb-1.0 -llua5.4 -ljsoncpp

.PHONY: all clean checksum install

all: $(TARGET)

$(TARGET): $(OBJS)
	$(CXX) $(CXXFLAGS) $(LDFLAGS) -o $@ $(OBJS) $(LDLIBS)

# 依赖头文件变更后重编：给出最小依赖（各 .cpp 就近包含同目录头）
%.o: %.cpp
	$(CXX) $(CPPFLAGS) $(CXXFLAGS) -c -o $@ $<

# 随包交付的校验和文件（DEP-04④）：make checksum → usb-proxy.sha256
checksum: $(TARGET)
	$(SHA256SUM) $(TARGET) > $(TARGET).sha256
	@echo "已生成 $(TARGET).sha256: $$(cat $(TARGET).sha256)"

# 部署到指定目录（默认就地，DESTDIR/PREFIX 可选）
install: $(TARGET)
	install -d "$(DESTDIR)$(PREFIX)/usbproxy"
	install -m 0755 $(TARGET) "$(DESTDIR)$(PREFIX)/usbproxy/$(TARGET)"

clean:
	rm -f $(OBJS) $(TARGET) $(TARGET).sha256
