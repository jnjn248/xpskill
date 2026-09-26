import argparse
import os
import sys
from collections import Counter
from pathlib import Path

from .analysis import analyse, sample_sessions
from .llm import consult_llm
from .package import upload_to_device, write_package
from .parsers import load_messages
from .privacy import redact
from .render import render_memory_md, render_skill_md


def build_args(argv=None):
    p = argparse.ArgumentParser(
        description="把聊天记录蒸馏成xinpai-bot可用的人设技能",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="输出：<out>/<name>/SKILL.md + references/memory.md，以及 <out>/<name>.zip",
    )
    p.add_argument("--input", required=True,
                   help="聊天记录文件或目录（txt/csv/json/html/mht/mbox/js/db）")
    p.add_argument("--name", required=True, help="技能目录名（字母数字下划线短横线点）")
    p.add_argument("--display", help="显示名（默认与 --name 相同）")
    p.add_argument("--me", default="我",
                   help="你自己在记录里的昵称（逗号分隔可多个，默认「我」）。"
                        "微信工具导出时本人一侧通常标成「我」，保持默认即可")
    p.add_argument("--target", help="要蒸馏的对象昵称（默认自动判断：除 --me 之外说话最多的人）")
    p.add_argument("--channel", help="微信 SQLite 数据库中的 StrTalker，会话对象（仅 --input 为 .db 时使用）")
    p.add_argument("--desc", default="", help="主观描述：性格/MBTI/星座/标签，例如『ENFP，双子座，话痨』")
    p.add_argument("--out", default=str(Path(__file__).resolve().parent.parent / "dist"),
                   help="输出目录（默认 <工具目录>/dist，建议显式指定；不要依赖系统盘根目录等绝对路径）")
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
