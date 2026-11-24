from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo


LOCAL_TZ = ZoneInfo("Asia/Shanghai")


# cp1252 字符 → 原始字节 的反向映射（字节 0x80~0xFF 经 cp1252 解码得到的字符）
_CP1252_REVERSE = {}
for _b in range(0x80, 0x100):
    try:
        _CP1252_REVERSE[bytes([_b]).decode("cp1252")] = _b
    except UnicodeDecodeError:
        pass  # cp1252 中 0x81/0x8D/0x8F/0x90/0x9D 未定义


def _mojibake_to_bytes(text: str) -> bytes | None:
    """把双重编码的乱码字符串逐字符还原成原始 UTF-8 字节。

    逐字符映射而非 str.encode()，因为乱码里可能混有 cp1252 特殊符号（如 ' ' U+2018）
    和 cp1252 未定义区间被保留的控制字符（U+008D 等），整串 encode 会失败。
    """
    out = bytearray()
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            out.append(code)
        elif ch in _CP1252_REVERSE:
            out.append(_CP1252_REVERSE[ch])
        elif 0x80 <= code <= 0x9F:
            out.append(code)  # cp1252 未定义字节被直接保留为同值控制字符
        else:
            return None
    return bytes(out)


def fix_mojibake(value: Any) -> Any:
    """修复双重 UTF-8 编码导致的中文乱码（如 'é™ˆæµ·é£Ž' → '陈海风'）。

    种子数据导入时 UTF-8 字节被当作 cp1252/latin-1 再编码一次，数据库里存的是乱码。
    这里在读取层把字符串还原成原始字节、再用 utf-8 解码还原中文。
    只对"能成功还原出中文"的字符串做转换，正常中文/英文/西欧文字原样返回。
    """
    if not isinstance(value, str) or not value:
        return value
    # 快速排除：不含 Latin-1 补充区字符（U+0080~U+00FF）的必然不是双重编码乱码
    if not any("\u0080" <= ch <= "\u00ff" for ch in value):
        return value
    raw = _mojibake_to_bytes(value)
    if raw is None:
        return value
    try:
        fixed = raw.decode("utf-8")
    except UnicodeDecodeError:
        return value
    # 只有还原结果确实含中文才采用，避免误伤合法的西欧文字（如 Café）
    if any("\u4e00" <= ch <= "\u9fff" for ch in fixed):
        return fixed
    return value


def parse_date(value: str, field_name: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{field_name} 格式必须为 YYYY-MM-DD") from exc


def parse_datetime(value: str, field_name: str) -> datetime:
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
        try:
            return datetime.strptime(value, fmt)
        except ValueError:
            continue
    raise ValueError(f"{field_name} 格式必须为 YYYY-MM-DD HH:MM:SS")


def offset_limit(page_no: int, page_size: int) -> tuple[int, int]:
    page_no = max(page_no, 1)
    page_size = max(min(page_size, 100), 1)
    return (page_no - 1) * page_size, page_size


def masked_identity(identity_no: str | None) -> str | None:
    if not identity_no:
        return None
    if len(identity_no) <= 4:
        return "*" * len(identity_no)
    if len(identity_no) <= 8:
        return identity_no[:2] + "*" * (len(identity_no) - 4) + identity_no[-2:]
    return identity_no[:3] + "*" * (len(identity_no) - 7) + identity_no[-4:]


def normalize_identity(identity_no: str) -> str:
    return identity_no.replace(" ", "").upper()


def make_no(prefix: str) -> str:
    return f"{prefix}{local_now().strftime('%Y%m%d%H%M%S%f')}"


def local_now() -> datetime:
    return datetime.now(LOCAL_TZ).replace(tzinfo=None)


def decimal_to_float(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def first_or_none(items: list[dict[str, Any]]) -> dict[str, Any] | None:
    return items[0] if items else None


def format_datetime(value: datetime | None) -> str | None:
    return value.strftime("%Y-%m-%d %H:%M:%S") if value is not None else None


def format_date(value: date | None) -> str | None:
    return value.strftime("%Y-%m-%d") if value is not None else None


def money(value: Decimal | float | int | None) -> float | None:
    if value is None:
        return None
    if isinstance(value, Decimal):
        return float(value)
    return float(Decimal(str(value)))


def count_total(sql: str, params: Any | None = None) -> int:
    from .database import fetch_one

    row = fetch_one(sql, params)
    if row is None or row["total"] is None:
        return 0
    return int(row["total"])
