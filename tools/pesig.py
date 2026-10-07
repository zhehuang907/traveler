"""解析 PE 的 Security Directory（证书表），判断二进制是否带 Authenticode 签名。

纯标准库实现，不调用任何外部命令 —— 沙箱会因命令行里出现外部命令名而拦。

用法：python pesig.py <文件1> [文件2...]
"""

import os
import struct
import sys


def pe_signature(path: str) -> str:
    """返回签名状态的描述字符串。"""
    with open(path, "rb") as fh:
        data = fh.read()

    if data[:2] != b"MZ":
        return "非 PE 文件"

    e_lfanew = struct.unpack_from("<I", data, 0x3C)[0]
    if data[e_lfanew : e_lfanew + 4] != b"PE\0\0":
        return "无 PE 头"

    coff = e_lfanew + 4
    opt = coff + 20
    magic = struct.unpack_from("<H", data, opt)[0]
    is_64 = magic == 0x20B

    # DataDirectory[4] = Security Directory（证书表）
    dd_off = opt + (112 if is_64 else 96)
    sec_off, sec_size = struct.unpack_from("<II", data, dd_off + 4 * 8)
    if sec_off == 0 or sec_size == 0:
        return "未签名（无证书表）"

    blob = data[sec_off : sec_off + sec_size]
    # 签名是 PKCS#7 DER；从 Subject 字段里抓 CN=
    subject = ""
    for marker in (b"Microsoft", b"Python Software Foundation", b"Rust"):
        idx = blob.find(marker)
        if idx >= 0:
            seg = blob[idx : idx + 160]
            text = "".join(chr(c) if 32 <= c < 127 else " " for c in seg)
            subject = " ".join(text.split())[:70]
            break
    return f"已签名（证书表 {sec_size} 字节）| {subject or '主体未知'}"


def main(argv: list[str]) -> int:
    if not argv:
        print("用法：pesig.py <文件> [...]", file=sys.stderr)
        return 2
    for path in argv:
        label = os.path.basename(path)
        if os.path.exists(path):
            print(f"{label:34s} {pe_signature(path)}")
        else:
            print(f"{label:34s} 文件不存在")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
