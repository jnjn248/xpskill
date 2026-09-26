import csv
import email
import email.header
import io
import json
import mailbox
import quopri
import re
import sqlite3
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path

from .models import Msg


TIME_PATTERNS = [
    re.compile(r"^(\d{4})[-/.](\d{1,2})[-/.](\d{1,2})[ T](\d{1,2}):(\d{2})(?::(\d{2}))?"),
    re.compile(r"^(\d{4})年(\d{1,2})月(\d{1,2})日\s*(\d{1,2}):(\d{2})(?::(\d{2}))?"),
    re.compile(r"^(\d{1,2})[-/.](\d{1,2})[ T](\d{1,2}):(\d{2})"),  # 无年份
]
LINE_WITH_NAME = re.compile(r"^(?P<who>[^:：\[\]]{1,24})\s*[:：]\s*(?P<text>.+)$")
WEIBO_EMOJI = re.compile(r"\[[^\[\]]{1,8}\]")
SENTENCE_SPLIT = re.compile(r"[。！？!?~～\.,，、\s]+")


def _mk_ts(groups) -> datetime | None:
    try:
        nums = [int(g) if g else 0 for g in groups]
        if len(nums) == 6:
            y, mo, d, h, mi, s = nums
        elif len(nums) == 4:  # 无年份
            y, mo, d, h, mi, s = datetime.now().year, nums[0], nums[1], nums[2], nums[3], 0
        else:
            return None
        if not (1970 <= y <= 2100 and 1 <= mo <= 12 and 1 <= d <= 31):
            return None
        return datetime(y, mo, d, h, min(mi, 59), min(s, 59))
    except Exception:
        return None


def _parse_time_prefix(line: str):
    stripped = line.lstrip()
    # 很多导出格式把时间放在中括号里：[2023-06-01 23:40] 昵称: 内容
    if stripped.startswith("["):
        end = stripped.find("]")
        if 0 < end <= 32:
            inner = stripped[1:end].strip()
            rest = stripped[end + 1:].lstrip()
            for pat in TIME_PATTERNS:
                m = pat.match(inner)
                if m:
                    ts = _mk_ts(m.groups())
                    if ts:
                        return ts, rest
    for pat in TIME_PATTERNS:
        m = pat.match(stripped)
        if m:
            ts = _mk_ts(m.groups())
            if ts:
                return ts, stripped[m.end():].strip()
    return None, line


def _pick_column(header: list[str], keys: tuple) -> int | None:
    """按 keys 的顺序挑第一个命中的列，保证优先取更可靠的那一列。"""
    for key in keys:
        for i, name in enumerate(header):
            if key in name:
                return i
    return None


# QQ 导出的 txt 里有大量分隔与说明行，必须丢掉，否则会混进消息正文
BANNER_KEYS = ("消息记录", "消息分组", "消息对象", "===========")


def _is_banner(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return True
    if len(stripped) >= 3 and set(stripped) <= set("=-= "):
        return True
    return any(stripped.startswith(key) for key in BANNER_KEYS)


def parse_csv(path: Path) -> list[Msg]:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    else:
        return []

    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(str(c).strip() for c in r)]
    if not rows:
        return []

    header = [str(c).strip().lower() for c in rows[0]]
    # 按优先级挑列：微信系工具导出的表头差异很大（WeChatMsg / 留痕 / PyWxDump…）
    idx_time = _pick_column(header, ("strtime", "createtime", "time", "date", "时间", "日期"))
    idx_who = _pick_column(header, ("nickname", "remark", "昵称", "备注", "sendername",
                                    "sender", "发送", "name", "talker", "用户", "from"))
    idx_text = _pick_column(header, ("strcontent", "content", "text", "消息", "内容",
                                     "正文", "displaycontent", "msg"))
    # WeChatMsg 之类会给出 IsSender：1=本人，0=对方；比靠昵称判断可靠得多
    idx_sender = _pick_column(header, ("issender", "is_sender"))
    body = rows[1:] if idx_text is not None else rows
    if idx_text is None:                       # 无表头：按 时间, 昵称, 内容 猜
        idx_time, idx_who, idx_text = 0, 1, 2

    out: list[Msg] = []
    for row in body:
        if len(row) <= max(filter(None, (idx_time, idx_who, idx_text)) or [0]):
            continue
        ts_raw = str(row[idx_time]).strip()
        ts = None
        for pat in TIME_PATTERNS:
            mm = pat.match(ts_raw)
            if mm:
                ts = _mk_ts(mm.groups())
                break
        if ts is None:
            try:
                ts = datetime.fromisoformat(ts_raw.replace("Z", ""))
            except Exception:
                ts = None
        who = str(row[idx_who]).strip() if idx_who is not None else ""
        if idx_sender is not None and len(row) > idx_sender:
            flag = str(row[idx_sender]).strip().lower()
            who = "我" if flag in ("1", "true", "yes") else (who or "对方")
        txt = str(row[idx_text]).strip() if idx_text is not None else ""
        if txt:
            out.append(Msg(ts, who or "未知", txt))
    return out


def _parse_any_timestamp(value) -> datetime | None:
    """兼容 Unix 秒/毫秒、ISO-8601、常见导出文本时间。"""
    if isinstance(value, (int, float)) and value > 0:
        try:
            return datetime.fromtimestamp(value / 1000 if value > 1e11 else value)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    text = value.strip()
    for pat in TIME_PATTERNS:
        match = pat.match(text)
        if match:
            ts = _mk_ts(match.groups())
            if ts:
                return ts
    for fmt in (
        "%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
        "%Y/%m/%d %H:%M:%S", "%Y/%m/%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        return None


def _json_text(value) -> str:
    """提取 Telegram 的 text 片段、Facebook/WhatsApp 的正文等。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
        return "".join(parts).strip()
    if isinstance(value, dict):
        return _json_text(value.get("text") or value.get("content") or value.get("body") or "")
    return ""


def _json_items(data):
    if isinstance(data, list):
        return data
    if not isinstance(data, dict):
        return []
    for key in ("messages", "list", "items", "posts", "comments", "statuses", "data"):
        value = data.get(key)
        if isinstance(value, list):
            return value
    return []


def parse_wechat_db(path: Path, channel: str | None = None) -> list[Msg]:
    """读取已解密的微信 EnMicroMsg.db 的 MSG 表。"""
    try:
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        where = "WHERE Type = 1"
        params: list[object] = []
        if channel:
            where += " AND StrTalker = ?"
            params.append(channel)
        rows = conn.execute(
            f"SELECT StrContent, CreateTime, IsSender, StrTalker FROM MSG {where} "
            "ORDER BY CreateTime ASC",
            params,
        ).fetchall()
        conn.close()
    except sqlite3.Error as exc:
        raise ValueError(f"无法读取微信 SQLite 数据库：{exc}") from exc

    out: list[Msg] = []
    for text, raw_ts, is_sender, talker in rows:
        text = str(text or "").strip()
        if not text:
            continue
        ts = None
        if isinstance(raw_ts, (int, float)) and raw_ts > 0:
            try:
                ts = datetime.fromtimestamp(raw_ts / 1000 if raw_ts > 1e11 else raw_ts)
            except (OverflowError, OSError, ValueError):
                pass
        speaker = "我" if is_sender == 1 else str(talker or "对方")
        out.append(Msg(ts, speaker, text))
    return out


def parse_qq_txt(path: Path, text: str) -> list[Msg]:
    """QQ 消息管理器导出（.txt，或 .mht 转出来的纯文本）。

    形如：
        2023-01-01 12:00:00 小雨(123456)
        在干嘛
    """
    out: list[Msg] = []
    cur_who, cur_ts = "", None
    qq_head = re.compile(r"^(?P<ts>\d{4}-\d{1,2}-\d{1,2}\s+\d{1,2}:\d{2}(?::\d{2})?)\s+(?P<who>.+?)(?:\(\d+\)|<[^>]+>)?\s*$")
    for line in text.splitlines():
        line = line.rstrip()
        if _is_banner(line):
            continue
        m = qq_head.match(line)
        if m:
            cur_ts = None
            for pat in TIME_PATTERNS:
                mm = pat.match(m.group("ts"))
                if mm:
                    cur_ts = _mk_ts(mm.groups())
                    break
            cur_who = m.group("who").strip()
            continue
        if cur_who:
            out.append(Msg(cur_ts, cur_who, line.strip()))
    return out


WHATSAPP_HEAD = re.compile(
    r"^\[?(?P<date>\d{1,4}[/-]\d{1,2}[/-]\d{1,4}),?\s+"
    r"(?P<time>\d{1,2}:\d{2}(?::\d{2})?(?:\s*[APap][Mm])?)\]?\s*"
    r"-?\s*(?P<who>[^:]+):\s*(?P<text>.*)$"
)


def parse_whatsapp_txt(text: str) -> list[Msg]:
    """解析 WhatsApp 导出 TXT，支持多行消息和 12/24 小时制。"""
    out: list[Msg] = []
    current: Msg | None = None
    for line in text.splitlines():
        match = WHATSAPP_HEAD.match(line.strip())
        if match:
            if current and current.text.strip():
                out.append(current)
            ts = None
            date_text, time_text = match.group("date"), match.group("time")
            for fmt in (
                "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M",
                "%d/%m/%y %H:%M:%S", "%d/%m/%y %H:%M",
                "%m/%d/%Y %I:%M %p", "%m/%d/%y %I:%M %p",
                "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M",
            ):
                try:
                    ts = datetime.strptime(f"{date_text} {time_text}", fmt)
                    break
                except ValueError:
                    continue
            current = Msg(ts, match.group("who").strip(), match.group("text").strip())
        elif current and line.strip():
            current.text += "\n" + line.strip()
    if current and current.text.strip():
        out.append(current)
    return out


def parse_generic_txt(text: str) -> list[Msg]:
    out: list[Msg] = []
    cur_ts, cur_who = None, ""
    for line in text.splitlines():
        line = line.strip()
        if _is_banner(line):
            continue
        ts, rest = _parse_time_prefix(line)
        if ts:
            cur_ts = ts
            nm = LINE_WITH_NAME.match(rest)
            if nm:
                cur_who = nm.group("who").strip()
                out.append(Msg(cur_ts, cur_who, nm.group("text").strip()))
                continue
            line = rest
        nm = LINE_WITH_NAME.match(line)
        if nm and len(nm.group("who")) <= 20:
            cur_who = nm.group("who").strip()
            out.append(Msg(cur_ts, cur_who, nm.group("text").strip()))
        elif out:
            out[-1].text += " " + line
    return out


def parse_json_log(path: Path) -> list[Msg]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except Exception:
        return []
    items = _json_items(data)
    out: list[Msg] = []
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        txt = _json_text(
            it.get("text") or it.get("body") or it.get("content")
            or it.get("message") or it.get("post") or it.get("full_text") or it.get("msg") or ""
        )

        # QQChatExporter stores sender as an object. Prefer the display name
        # and fall back through the other human-readable identity fields.
        who = (it.get("sender") or it.get("sender_name") or it.get("author")
               or it.get("user") or it.get("talker") or it.get("name")
               or it.get("from") or it.get("actor") or "")
        if isinstance(who, dict):
            who = (who.get("name") or who.get("remark") or who.get("nickname")
                   or who.get("uin") or who.get("uid") or "")
        t = it.get("created_at") or it.get("createdAt") or it.get("timestamp_ms")
        t = t if t is not None else (it.get("time") or it.get("timestamp") or it.get("date") or "")
        ts = _parse_any_timestamp(t)
        if txt:
            out.append(Msg(ts, str(who).strip() or "未知", txt))
    return out


def parse_twitter_js(path: Path) -> list[Msg]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    text = re.sub(r"^(?:window\.\w+\.\w+|\w+)\s*=\s*", "", text, count=1)
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []
    if path.name.lower() in ("direct-messages.js", "direct_messages.js"):
        items = []
        for conv in data if isinstance(data, list) else []:
            obj = conv.get("dmConversation", conv) if isinstance(conv, dict) else {}
            for entry in obj.get("messages", []):
                msg = entry.get("messageCreate", entry) if isinstance(entry, dict) else {}
                items.append({"text": msg.get("text"), "sender": msg.get("senderId"),
                              "createdAt": msg.get("createdAt")})
        return _messages_from_items(items)
    items = []
    for item in data if isinstance(data, list) else []:
        items.append(item.get("tweet", item) if isinstance(item, dict) else item)
    return _messages_from_items(items)


def _messages_from_items(items) -> list[Msg]:
    out = []
    for item in items:
        if not isinstance(item, dict):
            continue
        text = _json_text(item.get("text") or item.get("full_text") or item.get("body")
                          or item.get("content") or item.get("message") or item.get("post") or "")
        if not text:
            continue
        sender = item.get("sender") or item.get("sender_name") or item.get("author")
        if isinstance(sender, dict):
            sender = sender.get("name") or sender.get("username") or sender.get("id")
        ts = _parse_any_timestamp(item.get("createdAt") or item.get("created_at")
                                  or item.get("timestamp_ms") or item.get("timestamp")
                                  or item.get("date") or item.get("time"))
        out.append(Msg(ts, str(sender or "未知"), text))
    return out


def parse_mbox(path: Path) -> list[Msg]:
    out = []
    try:
        box = mailbox.mbox(str(path))
        for message in box:
            body = ""
            if message.is_multipart():
                for part in message.walk():
                    if part.get_content_type() == "text/plain":
                        payload = part.get_payload(decode=True)
                        if payload:
                            body = payload.decode(part.get_content_charset() or "utf-8", "replace")
                            break
            else:
                payload = message.get_payload(decode=True)
                if payload:
                    body = payload.decode(message.get_content_charset() or "utf-8", "replace")
            body = body.strip()
            if not body:
                continue
            subject = str(email.header.make_header(email.header.decode_header(message.get("Subject", ""))))
            if subject:
                body = f"{subject}\n\n{body}"
            sender = message.get("From", "")
            ts = None
            if message.get("Date"):
                try:
                    parsed = parsedate_to_datetime(message.get("Date"))
                    ts = parsed.replace(tzinfo=None) if parsed else None
                except (TypeError, ValueError, OverflowError):
                    pass
            out.append(Msg(ts, sender, body))
    except Exception as exc:
        raise ValueError(f"无法读取 mbox：{exc}") from exc
    return out


class _TextExtractor(HTMLParser):
    """把 HTML / MHT 抽成纯文本，块级标签换算行。"""

    BLOCK_TAGS = {"br", "p", "div", "tr", "li", "h1", "h2", "h3", "h4", "td", "table"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in self.BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        self.parts.append(data)


def strip_html(text: str) -> str:
    parser = _TextExtractor()
    try:
        parser.feed(text)
    except Exception:
        pass
    plain = "".join(parser.parts).replace("\u00a0", " ")
    plain = re.sub(r"[ \t]+", " ", plain)
    plain = re.sub(r"\n{3,}", "\n\n", plain)
    return plain


def decode_quoted_printable(text: str) -> str:
    """QQ 的 .mht 常用 quoted-printable，先还原成可读文本（没有转义就原样返回）。"""
    if not re.search(r"=[0-9A-Fa-f]{2}", text):
        return text
    try:
        return quopri.decodestring(text.encode("utf-8", "ignore")).decode("utf-8", "ignore")
    except Exception:
        return text


def read_text_any(path: Path) -> str | None:
    raw = path.read_bytes()
    # utf-8 必须排在 gb18030 前面：gb18030 几乎能"解码"任何字节，会掩盖真正的 UTF-8
    for enc in ("utf-8-sig", "utf-8", "gb18030", "utf-16"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return None


def load_messages(path: Path, channel: str | None = None) -> list[Msg]:
    if path.is_dir():
        msgs: list[Msg] = []
        for child in sorted(path.rglob("*")):
            if child.is_file() and child.suffix.lower() in (
                    ".txt", ".csv", ".json", ".mht", ".html", ".htm"):
                msgs.extend(load_messages(child, channel=channel))
        return msgs

    suffix = path.suffix.lower()
    if suffix in (".db", ".sqlite", ".sqlite3") or path.read_bytes()[:16] == b"SQLite format 3\x00":
        return parse_wechat_db(path, channel=channel)
    if suffix == ".csv":
        return parse_csv(path)
    if suffix == ".json":
        return parse_json_log(path)

    text = read_text_any(path)
    if text is None:
        return []
    if suffix in (".mht", ".html", ".htm"):
        if suffix == ".mht":
            text = decode_quoted_printable(text)
        text = strip_html(text)

    qq = parse_qq_txt(path, text)
    # 只有确实解析出说话人，才认为这是 QQ 导出格式；否则说明是别的排版
    if len(qq) >= 5 and any(m.speaker != "未知" for m in qq):
        return qq
    return parse_generic_txt(text)


# ----------------------------------------------------------------------------
# 二、统计画像（不花钱的那一半）
# ----------------------------------------------------------------------------
