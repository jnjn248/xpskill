import statistics

from .models import CONFLICT_WORDS, SESSION_GAP, STOP_PHRASES, Msg, Stats
from .parsers import SENTENCE_SPLIT, WEIBO_EMOJI


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
