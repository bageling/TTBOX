#!/usr/bin/env python3
"""生成 TTBox Test 1080p240 EDID（专用于 RK3588 HDMI RX 测试，非真实显示器）。

时序：1920x1080@240Hz，Reduced Blanking 风格，Pixel Clock = 583.20MHz（精确）
  H_total = 2160 (h_blank=240: offset 88 / sync 44 / back 108)
  V_total = 1125 (v_blank=45: front 3 / sync 5 / back 37)
  rate = 583200000 / (2160*1125) = 240.000 Hz
128B 单块（Base Block），标准 checksum；DTD1 真实声明 1920x1080@240Hz；
产品名 'TTBox1080p240'（13 字符上限，不冒充真实显示器）。

用法：python3 gen_ttbox_1080p240.py <out.bin>
"""
import sys


def pnp(word):
    """3 字母 PNP -> 2 字节。A=1..Z=26，5bit 编码。"""
    w = 0
    for ch in word:
        w = (w << 5) | (ord(ch) - ord('A') + 1)
    return [(w >> 8) & 0xFF, w & 0xFF]


def main(out_path):
    e = [0] * 128

    # 0-7 header
    e[0:8] = [0x00, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0x00]
    # 8-9 PNP 'AIB'（TTBox）
    e[8], e[9] = pnp('AIB')
    # 10-11 product 0x2400（240Hz 标识）
    e[10], e[11] = 0x00, 0x24
    # 12-15 serial
    e[12:16] = [0x01, 0x00, 0x00, 0x00]
    # 16 week, 17 year
    e[16], e[17] = 0x01, 2024 - 1990
    # 18-19 version 1.4
    e[18], e[19] = 0x01, 0x04
    # 20 input: 0x80 digital 8-bit
    e[20] = 0x80
    # 21-22 screen size cm (24.5" 16:9 ≈ 54x30)
    e[21], e[22] = 0x36, 0x1E
    # 23 gamma 2.2
    e[23] = 0x78
    # 24 features: RGB color + preferred timing + continuous + RGB 4:4:4
    e[24] = 0x5A
    # 25-26 chroma low
    e[25], e[26] = 0x96, 0x05
    # 27-34 chroma coords (rec709-ish)
    e[27:35] = [0x8F, 0x52, 0x33, 0x66, 0x9A, 0x3D, 0x40, 0x51]
    # 35-36 established timings I/II
    e[35], e[36] = 0x00, 0x00
    # 37 established timings III
    e[37] = 0x00
    # 38-53 standard timings (8 x none 0x01 0x01)
    e[38:54] = [0x01, 0x01] * 8

    # 54-71 DTD1: 1920x1080@240 (pix 58320 x 10kHz)
    d = [0] * 18
    pix = 58320  # 583.20 MHz
    d[0], d[1] = pix & 0xFF, (pix >> 8) & 0xFF          # 0xB0 0xE3
    h_act, h_blank = 1920, 240
    v_act, v_blank = 1080, 45
    d[2] = h_act & 0xFF
    d[3] = h_blank & 0xFF
    d[4] = ((h_act >> 8) & 0x0F) << 4 | ((h_blank >> 8) & 0x0F)
    d[5] = v_act & 0xFF
    d[6] = v_blank & 0xFF
    d[7] = ((v_act >> 8) & 0x0F) << 4 | ((v_blank >> 8) & 0x0F)
    h_off, h_wid = 88, 44
    v_off, v_wid = 4, 5
    d[8] = h_off & 0xFF
    d[9] = h_wid & 0xFF
    # EDID DTD 同步字段位布局：
    #   byte10 bits[7:6]=v_sync_offset 低2位  bits[5:4]=v_sync_pulse 低2位
    #          bits[3:2]=h_sync_offset 高2位  bits[1:0]=h_sync_pulse 高2位
    #   byte11 bits[7:6]=v_sync_offset 高2位  bits[5:4]=v_sync_pulse 高2位
    d[10] = ((v_off & 0x3) << 6) | ((v_wid & 0x3) << 4) | \
            (((h_off >> 8) & 0x3) << 2) | ((h_wid >> 8) & 0x3)
    d[11] = (((v_off >> 2) & 0x3) << 6) | (((v_wid >> 2) & 0x3) << 4)
    d[12], d[13] = 0x00, 0x00  # image size mm
    d[14], d[15] = 0x00, 0x00  # border
    d[16], d[17] = 0x00, 0x00  # progressive
    e[54:72] = d

    # 72-89 DTD2: Display Range Limits (48-240 Hz, 30-255 kHz, 600MHz)
    r = [0] * 18
    r[0:5] = [0x00, 0x00, 0x00, 0xFD, 0x00]
    r[5], r[6] = 48, 240          # V freq min/max
    r[7], r[8] = 30, 255          # H freq min/max
    r[9] = 600 // 20              # max dotclock / 20 MHz
    r[10], r[11] = 0x00, 0x0A
    r[12:18] = [0x20] * 6
    e[72:90] = r

    # 90-107 DTD3: Monitor Name 'TTBox1080p240'
    n = [0] * 18
    n[0:5] = [0x00, 0x00, 0x00, 0xFC, 0x00]
    name = b'TTBox1080p240'       # 13 字节
    n[5:5 + len(name)] = list(name)
    e[90:108] = n

    # 108-125 DTD4: 空白
    b = [0] * 18
    b[0:5] = [0x00, 0x00, 0x00, 0xFE, 0x00]
    b[5] = 0x0A
    b[6:18] = [0x20] * 12
    e[108:126] = b

    # 125 unused, 126 extension count = 0
    e[125], e[126] = 0x00, 0x00
    # 127 checksum
    e[127] = (-sum(e[0:127])) & 0xFF

    data = bytes(e)
    assert len(data) == 128
    with open(out_path, 'wb') as f:
        f.write(data)

    # 打印 hex（供 .hex）
    print('hex:', data.hex(' '))
    print('checksum byte: 0x%02x' % e[127])
    print('sum&0xFF: 0x%02x' % (sum(data) & 0xFF))
    # 帧率复核
    h_total = h_act + h_blank
    v_total = v_act + v_blank
    rate = pix * 10000.0 / (h_total * v_total)
    print('self-check: %dx%d@%.3f Hz pix=%.2f MHz' % (h_act, v_act, rate, pix / 100.0))


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
