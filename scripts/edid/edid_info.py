#!/usr/bin/env python3
"""EDID 信息解析工具（TTBox EDID 测试辅助，板端运行）。

用法:
  edid_info.py <edid.bin>
  edid_info.py <edid.hex>

输出: 厂商/型号/尺寸/接口/DTD 时序(分辨率@刷新率/像素时钟)/色彩格式/checksum 校验。
仅测试辅助，不进入正式 AI 高速链路。
"""
import sys


def decode_vendor(b8, b9):
    w = (b8 << 8) | b9
    return ''.join(chr(((w >> shift) & 0x1F) + 0x40)
                   for shift in (10, 5, 0))


def parse_dtd(b):
    """解析 18 字节 DTD（非描述符类型时）。返回 (w, h, hz, pix_mhz) 或 None。"""
    if b[0] == 0 and b[1] == 0:
        return None  # 描述符（name/serial/ranges...）
    pix = b[0] | (b[1] << 8)            # 像素时钟，10kHz 单位
    if pix == 0:
        return None
    w = b[2] | ((b[4] & 0xF0) << 4)     # h_active
    h = b[5] | ((b[7] & 0xF0) << 4)     # v_active
    h_total = (b[3] | ((b[4] & 0x0F) << 8)) + w
    v_total = (b[6] | ((b[7] & 0x0F) << 8)) + h
    hz = pix * 10000.0 / (h_total * v_total)  # pix(10kHz) * 1000 / total
    return w, h, hz, pix / 100.0


def parse_descriptor(b):
    """返回 (tag, text) 或 None。tag: fc=name ff=serial fd=ranges fe=空白。"""
    if b[0] == 0 and b[1] == 0:
        tag = b[3]
        if tag in (0xFC, 0xFF, 0xFE, 0xFD):
            text = bytes(b[5:18]).decode('latin1', 'replace').rstrip('\n\x00 ')
            return tag, text
    return None


def checksum_ok(block):
    return (sum(block) & 0xFF) == 0


def load_bin(path):
    raw = open(path, 'rb').read()
    if raw[:8] == b'\x00\xff\xff\xff\xff\xff\xff\x00':
        return raw
    # 尝试 hex 文本
    hexs = ''.join(l.strip() for l in open(path, encoding='utf-8', errors='ignore')
                   if l.strip() and all(c in '0123456789abcdefABCDEF ' for c in l.strip()))
    if hexs:
        return bytes.fromhex(hexs)
    raise SystemExit('无法解析输入（需要 .bin 或 hex 文本）')


def main(path):
    data = load_bin(path)
    n_blocks = len(data) // 128
    print('文件: %s  (%d 字节, %d 块)' % (path, len(data), n_blocks))
    ok = all(checksum_ok(data[i * 128:(i + 1) * 128]) for i in range(n_blocks))
    print('checksum: %s' % ('OK' if ok else 'FAIL'))

    b0 = data[0:128]
    print('厂商: %s  型号ID: 0x%04X' % (decode_vendor(b0[8], b0[9]),
                                      (b0[10] | (b0[11] << 8)) & 0xFFFF))
    # 接口（digital bytes 20-21：bit7=digital; 低3位=接口类型）
    d = b0[20]
    if d & 0x80:
        iface = {0x0a: 'HDMI', 0x0b: 'HDMI', 0x05: 'DisplayPort', 0x07: 'DisplayPort',
                 0x06: 'TMDS', 0x03: 'DVI'}.get(d & 0x0F, 'Digital(未知)')
    else:
        iface = 'Analog(VGA)'
    print('接口: %s' % iface)
    bits = b0[20] & 0x30 >> 4  # 粗略
    # 4 个 DTD 描述符
    print('DTD 时序:')
    for off in range(0x36, 0x36 + 4 * 18, 18):
        desc = parse_descriptor(b0[off:off + 18])
        if desc:
            tag, text = desc
            if tag == 0xFC:
                print('  型号: %s' % text)
            elif tag == 0xFF:
                print('  序列号: %s' % text)
            elif tag == 0xFD:
                print('  频率范围: %s' % text)
            continue
        r = parse_dtd(b0[off:off + 18])
        if r:
            w, h, hz, pix = r
            print('  %dx%d @ %.2f Hz  pix=%.3f MHz' % (w, h, hz, pix))
    # 扩展块中的 DTD（简化：CTA 块偏移 128+）
    for i in range(1, n_blocks):
        blk = data[i * 128:(i + 1) * 128]
        tag = blk[0]
        if tag == 0x02:  # CTA-861
            dtd_off = blk[2]
            if dtd_off and dtd_off < 118:
                for off in range(dtd_off, 126, 18):
                    r = parse_dtd(blk[off:off + 18])
                    if r:
                        w, h, hz, pix = r
                        print('  [CTA] %dx%d @ %.2f Hz  pix=%.3f MHz' % (w, h, hz, pix))
    # 色彩格式（数字显示器粗判：支持 RGB444 默认；详细需 CTA Colorimetry）
    print('色彩格式: RGB 4:4:4（详见 edid-decode 完整输出）')


if __name__ == '__main__':
    if len(sys.argv) != 2:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1])
