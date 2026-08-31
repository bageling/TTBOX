#!/usr/bin/env python3
"""EDID 校验/转换工具（TTBox EDID 测试辅助，板端运行）。

用法:
  edid_verify.py <edid.hex>            # 校验 hex → bin（打印块数与 checksum）
  edid_verify.py <edid.hex> -o <out.bin>  # 转换并写出 bin
  edid_verify.py <edid.bin>            # 校验 bin

仅测试辅助，不进入正式 AI 高速链路。
"""
import sys


def hex_to_bin(text):
    hexs = ''.join(l.strip() for l in text.splitlines()
                   if l.strip() and all(c in '0123456789abcdefABCDEF ' for c in l.strip()))
    if not hexs or len(hexs) % 2:
        raise SystemExit('无效 hex 内容')
    return bytes.fromhex(hexs)


def checksum_ok(block):
    return (sum(block) & 0xFF) == 0


def verify(data, label):
    n = len(data)
    print('%s: %d 字节' % (label, n))
    if n % 128:
        print('  FAIL: 长度不是 128 的倍数')
        return 1
    n_blocks = n // 128
    ok = all(checksum_ok(data[i * 128:(i + 1) * 128]) for i in range(n_blocks))
    print('  块数: %d | checksum: %s' % (n_blocks, 'OK' if ok else 'FAIL'))
    if data[:8] != b'\x00\xff\xff\xff\xff\xff\xff\x00':
        print('  WARN: 非标准 EDID header')
    return 0 if ok else 1


def main(argv):
    src = argv[0]
    out = None
    if '-o' in argv:
        out = argv[argv.index('-o') + 1]
    raw = open(src, 'rb').read()
    if raw[:8] == b'\x00\xff\xff\xff\xff\xff\xff\x00':
        data = raw
        rc = verify(data, src)
    else:
        text = raw.decode('utf-8', 'ignore')
        data = hex_to_bin(text)
        rc = verify(data, src)
    if out:
        open(out, 'wb').write(data)
        print('已写出: %s (%d 字节)' % (out, len(data)))
    return rc


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    sys.exit(main(sys.argv[1:]))
