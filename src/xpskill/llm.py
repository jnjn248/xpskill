import json
import re
import sys
import urllib.error
import urllib.request
from collections import defaultdict


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
