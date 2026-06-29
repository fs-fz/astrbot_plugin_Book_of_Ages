"""AstrBot 岁月史书插件。

将一组 QQ 号和文本转换为 OneBot v11 合并转发消息。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from astrbot.api import logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.message_components import Node, Nodes, Plain
from astrbot.api.star import Context, Star, register


COMMAND = "岁月史书"
MAX_RECORDS = 100
MAX_MESSAGE_LENGTH = 5000
QQ_PATTERN = re.compile(r"^[1-9]\d{4,11}$")
COMMAND_PATTERN = re.compile(r"^\s*/?岁月史书(?:\s+|$)")


class RecordFormatError(ValueError):
    """用户输入的聊天记录格式不正确。"""


@dataclass(frozen=True)
class ChatRecord:
    qq: str
    message: str
    name: str


def _first_value(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def _strip_code_fence(payload: str) -> str:
    payload = payload.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n?(.*?)\n?```", payload, re.DOTALL | re.IGNORECASE)
    return match.group(1).strip() if match else payload


def _normalize_items(data: Any) -> list[Any]:
    if isinstance(data, list):
        return data

    if not isinstance(data, dict):
        raise RecordFormatError("JSON 顶层必须是数组或对象。")

    for key in ("messages", "records", "data", "items"):
        if key in data:
            if not isinstance(data[key], list):
                raise RecordFormatError(f'字段 "{key}" 必须是数组。')
            return data[key]

    if any(key in data for key in ("qq", "uin", "user_id")):
        return [data]

    # 简写对象：{"123456": "你好", "654321": "世界"}
    return [{"qq": qq, "message": message} for qq, message in data.items()]


def _normalize_record(item: Any, index: int) -> ChatRecord:
    if isinstance(item, (list, tuple)):
        if len(item) not in (2, 3):
            raise RecordFormatError(f"第 {index} 条数组记录应为 [QQ, 消息] 或 [QQ, 消息, 昵称]。")
        qq, message = item[0], item[1]
        name = item[2] if len(item) == 3 else None
    elif isinstance(item, dict):
        qq = _first_value(item, ("qq", "uin", "user_id"))
        message = _first_value(item, ("message", "msg", "content", "text"))
        name = _first_value(item, ("name", "nickname", "nick"))
    else:
        raise RecordFormatError(f"第 {index} 条记录必须是对象或数组。")

    if isinstance(qq, bool) or qq is None:
        raise RecordFormatError(f"第 {index} 条记录缺少 QQ 号。")
    qq_text = str(qq).strip()
    if not QQ_PATTERN.fullmatch(qq_text):
        raise RecordFormatError(f"第 {index} 条记录的 QQ 号无效：{qq_text!r}。")

    if message is None:
        raise RecordFormatError(f"第 {index} 条记录缺少消息内容。")
    message_text = str(message).strip()
    if not message_text:
        raise RecordFormatError(f"第 {index} 条记录的消息不能为空。")
    if len(message_text) > MAX_MESSAGE_LENGTH:
        raise RecordFormatError(
            f"第 {index} 条消息过长，最多允许 {MAX_MESSAGE_LENGTH} 个字符。"
        )

    name_text = str(name).strip() if name is not None else qq_text
    if not name_text:
        name_text = qq_text

    return ChatRecord(qq=qq_text, message=message_text, name=name_text)


def _parse_line_records(payload: str) -> list[Any]:
    items: list[dict[str, str]] = []
    for line_number, raw_line in enumerate(payload.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        parts: list[str] | None = None
        for separator in ("|", "\t"):
            if separator in line:
                parts = line.split(separator, maxsplit=1)
                break
        if parts is None:
            match = re.match(r"^(\d{5,12})\s*[:：]\s*(.+)$", line)
            if match:
                parts = [match.group(1), match.group(2)]

        if parts is None:
            raise RecordFormatError(
                f"第 {line_number} 行格式错误，请使用“QQ|消息”。"
            )
        items.append({"qq": parts[0], "message": parts[1]})
    return items


def parse_records(payload: str) -> list[ChatRecord]:
    """解析 JSON 或逐行简写格式，并完成输入校验。"""
    payload = _strip_code_fence(payload)
    if not payload:
        raise RecordFormatError("未提供聊天记录。")

    if payload.startswith(("[", "{")):
        try:
            items = _normalize_items(json.loads(payload))
        except json.JSONDecodeError as exc:
            raise RecordFormatError(
                f"JSON 格式错误（第 {exc.lineno} 行，第 {exc.colno} 列）：{exc.msg}。"
            ) from exc
    else:
        items = _parse_line_records(payload)

    if not items:
        raise RecordFormatError("至少需要一条聊天记录。")
    if len(items) > MAX_RECORDS:
        raise RecordFormatError(f"一次最多生成 {MAX_RECORDS} 条聊天记录。")

    return [_normalize_record(item, index) for index, item in enumerate(items, start=1)]


def _extract_payload(message: str) -> str:
    """从原始纯文本消息中移除指令名，保留后续 JSON 的所有空白。"""
    match = COMMAND_PATTERN.match(message)
    return message[match.end() :] if match else message


HELP_TEXT = """📜 岁月史书
将 QQ 号与消息生成为 QQ 合并转发聊天记录（仅支持 OneBot v11）。

JSON 格式：
/岁月史书 [{"qq":"12345678","message":"你好"},{"qq":"87654321","message":"世界","name":"昵称"}]

也支持逐行简写：
/岁月史书
12345678|你好
87654321|世界

可用字段：
QQ：qq / uin / user_id
消息：message / msg / content / text
昵称（可选）：name / nickname / nick"""


@register(
    "astrbot_plugin_book_of_ages",
    "fs-fz",
    "根据 QQ 号和文本生成合并转发聊天记录",
    "1.0.0",
)
class BookOfAgesPlugin(Star):
    def __init__(self, context: Context):
        super().__init__(context)

    @filter.permission_type(filter.PermissionType.ADMIN)
    @filter.command(COMMAND)
    async def book_of_ages(self, event: AstrMessageEvent):
        """根据多组 QQ 号和消息生成合并转发聊天记录。"""
        payload = _extract_payload(event.get_message_str() or "")
        if not payload.strip() or payload.strip().lower() in {"help", "帮助", "-h", "--help"}:
            yield event.plain_result(HELP_TEXT)
            return

        platform = (event.get_platform_name() or "").lower()
        if platform not in {"aiocqhttp", "onebot"}:
            yield event.plain_result(
                f"❌ 当前平台 {platform or '未知'} 不支持合并转发消息；请在 OneBot v11 平台使用。"
            )
            return

        try:
            records = parse_records(payload)
            nodes = [
                Node(
                    uin=record.qq,
                    name=record.name,
                    content=[Plain(record.message)],
                )
                for record in records
            ]
            logger.info("岁月史书：准备生成 %d 条合并转发记录", len(nodes))
            yield event.chain_result([Nodes(nodes=nodes)])
        except RecordFormatError as exc:
            yield event.plain_result(f"❌ 输入格式错误：{exc}\n\n发送 /岁月史书 查看示例。")
        except Exception:
            logger.exception("岁月史书：生成合并转发消息失败")
            yield event.plain_result("❌ 生成聊天记录失败，请查看 AstrBot 日志。")
