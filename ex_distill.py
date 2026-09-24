#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ex_distill.py —— 把聊天记录蒸馏成「人设技能」，直接产出xinpai-bot可用的技能包。

为什么在 PC 上做：
  设备端只适合「读」技能，不适合跑「几万行聊天记录 + 多轮分析」的蒸馏流程。
  在这里蒸馏完，把结果（几 KB 文本）装到设备上即可，唤醒即生效。

设计要点：
  * 只用标准库（Python 3.9+），不需要 pip install 任何东西
  * LLM 走 OpenAI 兼容接口（阿里百炼 / DeepSeek / OpenAI 均可）；没配 key 也能出统计草稿
  * 输出结构对齐设备端设计：
        SKILL.md              → 人设（Part B），装成「主技能」，会话开始时注入指令
        references/memory.md  → 关系记忆（Part A），模型按需用 skill_search/skill_read 检索

典型用法：
  # 1) 只做统计（不联网、不花钱），先看看数据质量
  python3 ex_distill.py --input chat.txt --me 我 --name ex --no-llm

  # 2) 带 LLM 蒸馏（示例用阿里百炼的兼容接口）
  export MODELSCOPE_API_KEY=ms-xxxx
  python3 ex_distill.py --input wx.csv --me 我 --name ex \
      --desc "初恋，大学三年，ENFP，双子座，话痨，半夜爱发语音" \
      --base-url https://api-inference.modelscope.cn/v1 \
      --model Qwen/Qwen3-235B-A22B

  # 3) 蒸馏完直接传到设备（设备未加鉴权，局域网内可直接 POST）
  python3 ex_distill.py --input chat.txt --me 我 --name ex --desc "..." --device 192.168.137.103:8080

隐私提醒：
  加了 --llm 后，被选中的聊天片段会发送到你配置的 API 服务商。默认会先做脱敏
  （手机号、身份证、银行卡、邮箱），如不想脱敏加 --no-redact。
"""

from __future__ import annotations

import argparse
import csv
import email
import email.header
import io
import json
import mailbox
import os
import quopri
import re
import sqlite3
import statistics
import sys
import urllib.error
import urllib.request
import zipfile
from html.parser import HTMLParser
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# ----------------------------------------------------------------------------
# 数据结构
# ----------------------------------------------------------------------------

@dataclass
class Msg:
    ts: datetime | None
    speaker: str
    text: str


@dataclass
class Stats:
    total: int = 0
    per_speaker: Counter = field(default_factory=Counter)
    hours: Counter = field(default_factory=Counter)
    late_night: int = 0
    emoji: Counter = field(default_factory=Counter)
    phrases: Counter = field(default_factory=Counter)
    conflict_hits: Counter = field(default_factory=Counter)
    avg_len: float = 0.0
    session_starts: Counter = field(default_factory=Counter)
    first_ts: datetime | None = None
    last_ts: datetime | None = None


CONFLICT_WORDS = ["吵", "生气", "不理", "分手", "烦", "算了", "随便", "别说了",
                  "为什么", "冷战", "道歉", "对不起", "后悔"]
STOP_PHRASES = {"哈哈", "哈哈哈", "嗯嗯", "哦哦", "好的", "在的", "然后", "就是",
                "什么", "怎么", "可以", "没有", "不是", "我们", "你们", "他们",
                "哈哈哈哈哈", "这个", "那个", "现在", "已经", "还是", "真的"}

SESSION_GAP = timedelta(minutes=30)


# ----------------------------------------------------------------------------
# 一、解析聊天记录（多格式嗅探）
# ----------------------------------------------------------------------------

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

def analyse(msgs: list[Msg], target: str) -> Stats:
    st = Stats()
    st.total = len(msgs)
    lengths = []
    for m in msgs:
        st.per_speaker[m.speaker] += 1
        if m.ts:
            st.hours[m.ts.hour] += 1
            if m.ts.hour >= 23 or m.ts.hour <= 5:
                st.late_night += 1
            st.first_ts = min(st.first_ts, m.ts) if st.first_ts else m.ts
            st.last_ts = max(st.last_ts, m.ts) if st.last_ts else m.ts
        if m.speaker != target:
            continue
        lengths.append(len(m.text))
        for emo in WEIBO_EMOJI.findall(m.text):
            st.emoji[emo] += 1
        for word in CONFLICT_WORDS:
            if word in m.text:
                st.conflict_hits[word] += 1
        for seg in SENTENCE_SPLIT.split(m.text):
            seg = seg.strip()
            if 2 <= len(seg) <= 8 and seg not in STOP_PHRASES and not seg.isdigit():
                st.phrases[seg] += 1

    st.avg_len = round(statistics.mean(lengths), 1) if lengths else 0.0

    # 会话切分：>30 分钟算新会话，统计谁先开口（主动找人）
    prev = None
    for m in msgs:
        if m.ts and (prev is None or m.ts - prev > SESSION_GAP):
            st.session_starts[m.speaker] += 1
        if m.ts:
            prev = m.ts
    return st


def stats_markdown(st: Stats, target: str) -> str:
    span = ""
    if st.first_ts and st.last_ts:
        days = (st.last_ts - st.first_ts).days + 1
        span = f"{st.first_ts:%Y-%m-%d} ~ {st.last_ts:%Y-%m-%d}（约 {days} 天）"
    top_phrases = [p for p, c in st.phrases.most_common(18) if c >= 3]
    top_emoji = [f"{e}×{c}" for e, c in st.emoji.most_common(12)]
    late_ratio = f"{st.late_night / st.total * 100:.0f}%" if st.total else "0%"
    lines = [
        "## 数据统计（脚本自动提取，未经过 LLM，可当作风味的客观线索）",
        "",
        f"- 消息总数：{st.total}；时间跨度：{span or '未知'}",
        f"- 双方消息量：{'、'.join(f'{k} {v} 条' for k, v in st.per_speaker.most_common())}",
        f"- {target} 的主动开口次数：{st.session_starts.get(target, 0)} 次"
        f"（对方 {sum(v for k, v in st.session_starts.items() if k != target)} 次）",
        f"- {target} 平均消息长度：{st.avg_len} 字",
        f"- 深夜（23:00–05:00）消息占比：{late_ratio}",
    ]
    if top_phrases:
        lines.append(f"- 高频口头禅候选：{'、'.join(top_phrases)}")
    if top_emoji:
        lines.append(f"- 常用表情/表情包记号：{'、'.join(top_emoji)}")
    if st.conflict_hits:
        lines.append(f"- 冲突相关词频：{'、'.join(f'{k}×{v}' for k, v in st.conflict_hits.most_common(8))}")
    return "\n".join(lines)


# ----------------------------------------------------------------------------
# 三、采样：按会话切分，优先深夜 / 冲突 / 长会话
# ----------------------------------------------------------------------------

def sample_sessions(msgs: list[Msg], target: str, budget_chars: int) -> tuple[str, int]:
    sessions: list[list[Msg]] = []
    cur: list[Msg] = []
    prev = None
    for m in msgs:
        if m.ts and prev and m.ts - prev > SESSION_GAP:
            if cur:
                sessions.append(cur)
            cur = []
        cur.append(m)
        if m.ts:
            prev = m.ts
    if cur:
        sessions.append(cur)

    def priority(sess: list[Msg]) -> float:
        score = 0.0
        text = " ".join(m.text for m in sess)
        hit_target = sum(1 for m in sess if m.speaker == target)
        if not hit_target:
            return -1
        score += min(len(sess), 60) * 0.3            # 有来有回的长会话更有信息量
        score += hit_target * 0.2
        if any(m.ts and (m.ts.hour >= 23 or m.ts.hour <= 5) for m in sess):
            score += 25                               # 深夜对话最能体现真实性格
        score += sum(6 for w in CONFLICT_WORDS if w in text)   # 争吵/矛盾
        return score

    ranked = sorted(sessions, key=priority, reverse=True)
    chunks: list[str] = []
    used = 0
    for sess in ranked:
        if priority(sess) < 0:
            continue
        body = "\n".join(
            f"[{m.ts:%m-%d %H:%M}] {m.speaker}: {m.text}" if m.ts else f"{m.speaker}: {m.text}"
            for m in sess
        )
        if used + len(body) > budget_chars and chunks:
            break
        chunks.append(body)
        used += len(body)
    return "\n\n---\n\n".join(chunks), used


# ----------------------------------------------------------------------------
# 四、脱敏
# ----------------------------------------------------------------------------

REDACTIONS = [
    (re.compile(r"1[3-9]\d{9}"), "[手机号]"),
    (re.compile(r"\b\d{17}[\dXx]\b"), "[身份证]"),
    (re.compile(r"\b\d{16,19}\b"), "[卡号]"),
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"), "[邮箱]"),
    (re.compile(r"(?:[\u4e00-\u9fa5]{2,8}(?:省|市|区|县|镇|街道|小区|大厦|公寓)){2,}"), "[详细地址]"),
]


def redact(text: str) -> str:
    for pat, tag in REDACTIONS:
        text = pat.sub(tag, text)
    return text


# ----------------------------------------------------------------------------
# 五、LLM（OpenAI 兼容接口，标准库实现）
# ----------------------------------------------------------------------------

PERSONA_PROMPT = """你在做「人格蒸馏」：从一段真实聊天记录里，还原 TA 这个人说话和行为的样子。
只输出 JSON，不要任何解释。字段要求：

{
  "说话风格": ["短句/长句、标点习惯、语气词、有没有错别字、爱不爱用表情、爱不爱发语音文字描述", "..."],
  "口头禅": ["原样引用的高频口头语或句式，最多 12 条"],
  "情感模式": ["怎么表达关心、生气、开心、失落；回避还是直球", "..."],
  "关系行为": ["主动找人吗、回消息快慢、吵架后怎么收场、纪念日/生日的做法", "..."],
  "硬规则": ["像 TA 说话时必须遵守的底线，例如『从不说肉麻的话』『不会秒回，通常隔几分钟』", "..."],
  "典型例句": ["能体现 TA 风格的原话，3-8 条，尽量原样"]
}

要求：所有内容都必须能从聊天记录里找到依据，不确定就不要写，不要编造。
聊天记录中被标记为「{target}」的一方就是要蒸馏的对象。"""

MEMORY_PROMPT = """你在整理「关系记忆档案」：把聊天记录里的共同经历提取成结构化条目。
只输出 JSON，不要任何解释。字段要求：

{
  "关系时间线": ["YYYY-MM 或 大致时间 + 发生了什么（在一起/异地/争吵/和好等）", "..."],
  "一起去过的地方": ["地点 + 当时的细节", "..."],
  "inside_jokes": ["只有你们懂的梗，附上来历", "..."],
  "争吵模式": ["因为什么吵、怎么升级、怎么和好", "..."],
  "甜蜜瞬间": ["具体场景 + 原话片段", "..."],
  "称呼与专属用语": ["互相怎么叫、专属词汇", "..."]
}

只写聊天记录里真实出现的，不要脑补；没有的就给空数组。"""


def llm_call(base_url: str, api_key: str, model: str, system: str, user: str,
             timeout: int = 180, dry_run: bool = False) -> str:
    url = base_url.rstrip("/") + "/chat/completions"
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.3,
    }
    if dry_run:
        print(f"[dry-run] POST {url}  ({len(user)} 字符)", file=sys.stderr)
        return "{}"
    req = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:500]
        raise RuntimeError(f"LLM HTTP {exc.code} from {url}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"LLM connection failed for {url}: {exc.reason}") from exc
    if not raw.strip():
        raise RuntimeError(f"LLM returned an empty response from {url}")
    try:
        body = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"LLM returned non-JSON from {url}: {raw[:500]}") from exc
    try:
        return body["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"LLM response missing choices/message/content: {raw[:500]}") from exc


def extract_json(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        text = text[start:end + 1]
    try:
        return json.loads(text)
    except Exception:
        return {}


def merge_dicts(items: list[dict]) -> dict:
    merged: dict[str, list[str]] = defaultdict(list)
    for item in items:
        if not isinstance(item, dict):
            continue
        for key, value in item.items():
            if isinstance(value, str):
                value = [value]
            if not isinstance(value, list):
                continue
            for entry in value:
                entry = str(entry).strip()
                if entry and entry not in merged[key]:
                    merged[key].append(entry)
    return dict(merged)


def consult_llm(corpus: str, target: str, args, stats: Stats) -> tuple[dict, dict]:
    """把语料分片喂给 LLM，再合并成 persona / memory 两份结构化结果。"""
    limit = args.llm_chars
    pieces = []
    cur = []
    size = 0
    for para in corpus.split("\n\n"):
        if size + len(para) > limit and cur:
            pieces.append("\n\n".join(cur))
            cur, size = [], 0
        cur.append(para)
        size += len(para)
    if cur:
        pieces.append("\n\n".join(cur))
    pieces = pieces[: max(1, args.llm_batches)]

    personas, memories = [], []
    for i, piece in enumerate(pieces, 1):
        print(f"  · 第 {i}/{len(pieces)} 批：人格分析…", file=sys.stderr)
        out = llm_call(args.base_url, args.api_key, args.model,
                       PERSONA_PROMPT.replace("{target}", target), piece,
                       dry_run=args.dry_run_llm)
        personas.append(extract_json(out))
        print(f"  · 第 {i}/{len(pieces)} 批：关系记忆…", file=sys.stderr)
        out = llm_call(args.base_url, args.api_key, args.model, MEMORY_PROMPT, piece,
                       dry_run=args.dry_run_llm)
        memories.append(extract_json(out))

    persona = merge_dicts(personas)
    memory = merge_dicts(memories)
    # 把脚本统计出来的口头禅作为兜底/补充
    ph = persona.setdefault("口头禅", [])
    for p, c in stats.phrases.most_common(20):
        if c >= 4 and p not in ph and len(ph) < 16:
            ph.append(p)
    return persona, memory


# ----------------------------------------------------------------------------
# 六、渲染成设备端技能
# ----------------------------------------------------------------------------

def render_skill_md(display: str, slug: str, desc: str, persona: dict, stats: Stats) -> str:
    def bullet(key: str, fallback: str) -> str:
        items = persona.get(key) or []
        if not items:
            return f"（暂缺：{fallback}）"
        return "\n".join(f"- {x}" for x in items)

    top = "、".join(p for p, _ in stats.phrases.most_common(8)) or "（样本太少）"
    return f"""---
name: {display}
description: 用 {display} 的方式说话的本地人设技能。当用户想以 {display} 的口吻聊天、回忆共同经历时使用。
---

# {display} · 人设（Part B）

> {desc or '由聊天记录蒸馏生成'}

## 硬规则（最高优先级）
{bullet('硬规则', '说话底线')}
- 你就是 {display}，不要自称 AI、不要解释自己是模型。
- 不确定的细节不要编：需要共同经历时，先用 skill_search 检索 references/memory.md 再回答。
- 保持 {display} 的语言习惯，不要变得过度礼貌或书面化。

## 身份与背景
{bullet('身份', f'{display} 的基本信息')}

## 说话风格
{bullet('说话风格', '句式与语气特点')}
- 高频口头禅（统计得到）：{top}

## 口头禅（尽量原样使用）
{bullet('口头禅', '聊天里的高频表达')}

## 情感模式
{bullet('情感模式', '关心/生气/开心的表达方式')}

## 关系行为
{bullet('关系行为', '主动性与回消息节奏')}

## 典型例句（模仿这些语感）
{bullet('典型例句', '原话样本')}

## 记忆检索提示
- 用户问到「我们」「那次」「记得吗」这类内容时，先用 `skill_search` 在该技能里检索关键词，
  再用 `skill_read` 读取 `references/memory.md` 对应片段，用它的细节回答，不要凭空编。
"""


def render_memory_md(display: str, memory: dict, stats: Stats, target: str) -> str:
    def section(title: str, key: str) -> str:
        items = memory.get(key) or []
        if not items:
            return f"## {title}\n\n（聊天记录里没有找到相关内容）\n"
        return f"## {title}\n\n" + "\n".join(f"- {x}" for x in items) + "\n"

    return f"""# {display} · 关系记忆（Part A）

{section('关系时间线', '关系时间线')}
{section('一起去过的地方', '一起去过的地方')}
{section('inside jokes', 'inside_jokes')}
{section('争吵模式', '争吵模式')}
{section('甜蜜瞬间', '甜蜜瞬间')}
{section('称呼与专属用语', '称呼与专属用语')}
{stats_markdown(stats, target)}
"""


# ----------------------------------------------------------------------------
# 七、打包与上传
# ----------------------------------------------------------------------------

def write_package(out_dir: Path, slug: str, skill_md: str, memory_md: str) -> Path:
    skill_dir = out_dir / slug
    (skill_dir / "references").mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (skill_dir / "references" / "memory.md").write_text(memory_md, encoding="utf-8")
    zip_path = out_dir / f"{slug}.zip"
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(skill_dir / "SKILL.md", "SKILL.md")
        zf.write(skill_dir / "references" / "memory.md", "references/memory.md")
    return zip_path


def upload_to_device(device: str, slug: str, zip_path: Path) -> bool:
    url = f"http://{device}/api/skills/upload"
    data = zip_path.read_bytes()
    req = urllib.request.Request(
        url, data=data,
        headers={"Content-Type": "application/zip", "X-Skill-Name": slug},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            print(f"  · 上传结果：{resp.status} {resp.read().decode('utf-8', 'ignore')[:200]}")
        return True
    except urllib.error.HTTPError as e:
        print(f"  ! 上传失败：{e.code} {e.read().decode('utf-8', 'ignore')[:200]}", file=sys.stderr)
    except Exception as e:
        print(f"  ! 上传失败：{e}", file=sys.stderr)
    return False


# ----------------------------------------------------------------------------
# main
# ----------------------------------------------------------------------------

def build_args(argv=None):
    p = argparse.ArgumentParser(
        description="把聊天记录蒸馏成xinpai-bot可用的人设技能",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="输出：<out>/<name>/SKILL.md + references/memory.md，以及 <out>/<name>.zip",
    )
    p.add_argument("--input", required=True, help="聊天记录文件或目录（txt/csv/json）")
    p.add_argument("--name", required=True, help="技能目录名（字母数字下划线短横线点）")
    p.add_argument("--display", help="显示名（默认与 --name 相同）")
    p.add_argument("--me", default="我",
                   help="你自己在记录里的昵称（逗号分隔可多个，默认「我」）。"
                        "微信工具导出时本人一侧通常标成「我」，保持默认即可")
    p.add_argument("--target", help="要蒸馏的对象昵称（默认自动判断：除 --me 之外说话最多的人）")
    p.add_argument("--channel", help="微信 SQLite 数据库中的 StrTalker，会话对象（仅 --input 为 .db 时使用）")
    p.add_argument("--desc", default="", help="主观描述：性格/MBTI/星座/标签，例如『ENFP，双子座，话痨』")
    p.add_argument("--out", default="/home/jn/Echo-Mate/tools/dist",
                   help="输出目录（默认 /home/jn/Echo-Mate/tools/dist）")
    p.add_argument("--llm-chars", type=int, default=30000, help="送给 LLM 的语料字符预算（默认 30000）")
    p.add_argument("--llm-batches", type=int, default=3, help="最多分几批分析（默认 3）")
    p.add_argument("--base-url", default=os.environ.get("LLM_BASE_URL", "https://api-inference.modelscope.cn/v1"),
                   help="OpenAI 兼容接口地址")
    p.add_argument("--model", default=os.environ.get("LLM_MODEL", "Qwen/Qwen3-235B-A22B"), help="模型名")
    p.add_argument("--api-key", default=os.environ.get("LLM_API_KEY")
                   or os.environ.get("MODELSCOPE_API_KEY")
                   or os.environ.get("DASHSCOPE_API_KEY")
                   or os.environ.get("OPENAI_API_KEY", ""),
                   help="API Key（也可用环境变量 LLM_API_KEY / MODELSCOPE_API_KEY / DASHSCOPE_API_KEY / OPENAI_API_KEY）")
    p.add_argument("--no-llm", action="store_true", help="不调用 LLM，只输出统计与骨架")
    p.add_argument("--dry-run-llm", action="store_true", help="打印将要发送的请求，但不真正调用")
    p.add_argument("--no-redact", action="store_true", help="不做脱敏（默认会脱敏手机号/身份证/邮箱等）")
    p.add_argument("--device", help="可选：生成后直接上传，例如 192.168.137.103:8080")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = build_args(argv)
    src = Path(args.input)
    if not src.exists():
        print(f"找不到输入：{src}", file=sys.stderr)
        return 2

    print(f"[1/5] 解析聊天记录：{src}")
    try:
        msgs = load_messages(src, channel=args.channel)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    if not msgs:
        print("没解析出任何消息。支持的格式：微信/QQ 导出的 txt、csv、json；"
              "纯文本请写成「昵称: 内容」每行一条。", file=sys.stderr)
        return 2

    me = {x.strip() for x in args.me.split(",") if x.strip()}
    speakers = Counter(m.speaker for m in msgs)
    target = args.target
    if not target:
        candidates = [(n, c) for n, c in speakers.most_common() if n not in me]
        if not candidates:
            print(f"无法判断要蒸馏谁。所有说话者：{list(speakers)}，请用 --target 指定。", file=sys.stderr)
            return 2
        target = candidates[0][0]
    if target not in speakers:
        print(f"--target {target} 不在记录里。说话者：{list(speakers)}", file=sys.stderr)
        return 2

    print(f"      共 {len(msgs)} 条消息；说话者 {dict(speakers.most_common())}")
    print(f"      蒸馏对象：{target}")

    print("[2/5] 统计画像")
    stats = analyse(msgs, target)
    print(f"      口头禅候选：{'、'.join(p for p, _ in stats.phrases.most_common(8)) or '（不足）'}")
    print(f"      深夜消息占比：{stats.late_night / max(1, stats.total) * 100:.0f}%")

    print("[3/5] 挑选代表片段")
    corpus, used = sample_sessions(msgs, target, args.llm_chars)
    if not args.no_redact:
        corpus = redact(corpus)
    print(f"      选中 {used} 字符（深夜 / 争吵 / 长会话优先）")

    persona: dict = {}
    memory: dict = {}
    use_llm = not args.no_llm
    if use_llm and not args.api_key and not args.dry_run_llm:
        print("      未配置 API Key（--api-key、MODELSCOPE_API_KEY 或其他支持的环境变量），退化为统计模式", file=sys.stderr)
        use_llm = False

    if use_llm:
        print(f"[4/5] LLM 蒸馏（{args.model} @ {args.base_url}）")
        try:
            persona, memory = consult_llm(corpus, target, args, stats)
        except Exception as e:
            print(f"      LLM 调用失败：{e}", file=sys.stderr)
            print("      改为只输出统计骨架，可稍后重试", file=sys.stderr)
            persona, memory = {}, {}
    else:
        print("[4/5] 跳过 LLM（统计模式：SKILL.md 只有骨架，建议补 --desc 描述）")

    display = args.display or args.name
    skill_md = render_skill_md(display, args.name, args.desc, persona, stats)
    memory_md = render_memory_md(display, memory, stats, target)

    print("[5/5] 打包")
    zip_path = write_package(Path(args.out), args.name, skill_md, memory_md)
    print(f"      {zip_path}（{zip_path.stat().st_size / 1024:.1f} KB）")

    if args.device:
        print(f"      上传到 {args.device} …")
        upload_to_device(args.device, args.name, zip_path)

    print()
    print("下一步：")
    print("  1) 打开设备控制台（板子 → 设置 → 助手 扫码，或直接访问 http://<设备IP>:8080）")
    print(f"  2) 上传 {zip_path.name}")
    print("  3) 在技能列表里把它设为「主技能」（人设类）——之后唤醒即生效")
    print("  4) 想改口味就编辑 SKILL.md / references/memory.md 再传一次，或直接说『ta不会这样说』后手改")
    return 0


if __name__ == "__main__":
    sys.exit(main())
