from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta


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
