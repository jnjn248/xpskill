# xpskill

<div align="center"><img alt="License" src="https://img.shields.io/badge/license-MIT-green.svg"><img alt="Language" src="https://img.shields.io/badge/language-Python-3776AB.svg"></div>

<p align="center">🇺🇸 <a href="./README.en.md">English</a> | 🇨🇳 <a href="./README.md">简体中文</a></p>

一个聊天人格Skill包生成工具，将QQ等来源的聊天记录蒸馏为轻量、便于上传和迁移的`SKILL.md`、关系记忆文件及`.zip`技能包。生成结果可用于支持Skill格式或类似技能包机制的工具，当前主要适配xinpai-bot（项目待开源）。

> [!NOTE]
> 本文中的昵称、技能名、路径和 API Key 均为占位符。请替换为你自己的值，并在上传前检查生成文件中的个人信息。

## 目录

- [功能](#功能)
- [安装](#安装)
- [快速开始](#快速开始)
- [支持的输入](#支持的输入)
- [命令行参考](#命令行参考)
- [LLM 配置](#llm-配置)
- [输出](#输出)
- [隐私与安全](#隐私与安全)
- [开发与验证](#开发与验证)
- [贡献](#贡献)
- [许可证](#许可证)

## 功能


- 本地统计消息量、时间跨度、深夜占比、主动开口次数、平均消息长度、口头禅和冲突线索。
- 按会话切分样本，并优先选择深夜、冲突和较长的对话。
- 可选调用 OpenAI 兼容接口，分别提炼人设和关系记忆。
- 即使没有 API Key、LLM 限流或返回异常，也会生成可编辑的统计骨架。
- 默认对发送给 LLM 的样本脱敏，并可选择直接上传到设备。

## 安装

运行时只需要 Python 3.9+ 标准库，不需要 `pip install` 或第三方依赖。

```bash
cd /home/jn/Echo-Mate/tools/xpskill
python3 -m py_compile ex_distill.py
```

## 快速开始

### 仅本地统计

`--no-llm` 不联网、不调用模型，只解析消息并生成统计骨架：

```bash
python3 ex_distill.py \
  --input /path/to/chat.json \
  --me '你的昵称' \
  --target '聊天对象昵称' \
  --name chat-persona \
  --out /path/to/output \
  --no-llm
```

如果省略 `--target`，脚本会从非 `--me` 的说话者中选择消息最多的人。

### 使用 LLM 蒸馏

脚本向 OpenAI 兼容的 `/chat/completions` 接口发送代表性片段。使用环境变量保存密钥：

```bash
export MODELSCOPE_API_KEY="<YOUR_API_KEY>"

python3 ex_distill.py \
  --input /path/to/chat.json \
  --me '你的昵称' \
  --target '聊天对象昵称' \
  --name chat-persona \
  --desc '关系和说话风格的补充描述' \
  --base-url 'https://api-inference.modelscope.cn/v1' \
  --model 'Qwen/Qwen3-235B-A22B' \
  --out /path/to/output
```

### 直接上传设备

`--device` 会把生成的 zip 以 `POST /api/skills/upload` 上传到指定设备：

```bash
python3 ex_distill.py \
  --input /path/to/chat.json \
  --me '你的昵称' \
  --target '聊天对象昵称' \
  --name chat-persona \
  --out /path/to/output \
  --device '<DEVICE_IP>:8080'
```

## 支持的输入

| 类型 | 示例 | 解析行为 |
| --- | --- | --- |
| JSON | `--input chat.json` | 查找 `messages`、`list`、`items`、`posts`、`comments`、`statuses` 或 `data` 消息数组。 |
| CSV | `--input chat.csv` | 自动识别时间、发送者、正文和 `IsSender` 等常见列名。 |
| TXT | `--input chat.txt` | 支持 `昵称: 内容`、带时间的消息行和 QQ 导出文本。 |
| HTML/MHT | `--input chat.html` | 先提取纯文本，再按聊天消息格式解析。 |
| 微信 SQLite | `--input EnMicroMsg.db` | 读取已解密数据库的 `MSG` 表；可用 `--channel` 过滤 `StrTalker`。 |
| 目录 | `--input exports/` | 递归读取 `.txt`、`.csv`、`.json`、`.html`、`.htm`、`.mht` 文件。 |

JSON 消息正文支持 `text`、`content`、`message`、`body` 等字段，发送者支持 `name`、`remark`、`nickname`、`uin` 等字段。

## 命令行参考

| 参数 | 默认值 | 说明 |
| --- | --- | --- |
| `--input` | 必填 | 聊天记录文件或目录。 |
| `--name` | 必填 | 技能目录名和 zip 名称。 |
| `--display` | `--name` | 设备中显示的技能名称。 |
| `--me` | `我` | 本人在记录中的昵称；可用逗号分隔多个名称。 |
| `--target` | 自动判断 | 要蒸馏的对象；省略时选择消息最多的非本人说话者。 |
| `--channel` | 无 | 微信 SQLite 的 `StrTalker` 会话名。 |
| `--desc` | 空 | 对关系、性格或背景的补充描述。 |
| `--out` | `/home/jn/Echo-Mate/tools/dist` | 输出目录，建议显式指定。 |
| `--llm-chars` | `30000` | LLM 样本字符预算。 |
| `--llm-batches` | `3` | 最多分析批次数。 |
| `--base-url` | ModelScope 兼容地址 | OpenAI 兼容接口根地址。 |
| `--model` | `Qwen/Qwen3-235B-A22B` | 模型名称。 |
| `--api-key` | 环境变量 | API Key；支持 `LLM_API_KEY`、`MODELSCOPE_API_KEY`、`DASHSCOPE_API_KEY`、`OPENAI_API_KEY`。 |
| `--no-llm` | 关闭 | 只做本地统计并生成骨架。 |
| `--dry-run-llm` | 关闭 | 打印请求信息但不真正调用接口。 |
| `--no-redact` | 关闭 | 关闭默认的手机号、身份证、卡号、邮箱和详细地址脱敏。 |
| `--device` | 无 | 生成后上传到 `<设备IP>:8080`。 |

完整帮助：

```bash
python3 ex_distill.py --help
```

## LLM 配置

参数优先级如下：

| 配置 | 读取顺序 |
| --- | --- |
| API Key | `--api-key` → `LLM_API_KEY` → `MODELSCOPE_API_KEY` → `DASHSCOPE_API_KEY` → `OPENAI_API_KEY` |
| 接口地址 | `--base-url` → `LLM_BASE_URL` → ModelScope 默认地址 |
| 模型名 | `--model` → `LLM_MODEL` → `Qwen/Qwen3-235B-A22B` |

每个样本批次分别请求人格分析和关系记忆分析。可以缩小请求：

```bash
python3 ex_distill.py ... --llm-chars 12000 --llm-batches 1
```

使用 `--dry-run-llm` 可检查请求 URL 和样本长度而不发送聊天内容。

## 输出

每次运行会生成：

```text
<out>/<name>/SKILL.md
<out>/<name>/references/memory.md
<out>/<name>.zip
```

| 文件 | 内容 |
| --- | --- |
| `SKILL.md` | 人设技能：身份、说话风格、口头禅、情感模式、关系行为、硬规则和典型例句。 |
| `references/memory.md` | 关系时间线、共同地点、内部玩笑、争吵模式、甜蜜瞬间、称呼和统计数据。 |
| `<name>.zip` | 包含以上两个文件，可上传到设备。 |

没有 API Key、使用 `--no-llm` 或 LLM 失败时，仍会写入统计结果和可编辑骨架。

## 隐私与安全

> [!WARNING]
> 聊天记录可能包含高度敏感的个人信息。上传或调用外部 LLM 前，请先确认数据授权和服务商政策。

- `--no-llm` 模式下，聊天内容只在本机处理。
- 默认只把抽取出的代表片段发送给指定 LLM，并先脱敏常见手机号、身份证、银行卡、邮箱和详细地址。
- `--no-redact` 会发送未经脱敏的样本，仅在风险可接受时使用。
- 不要把 API Key 写入 README、源代码或 Git 提交；使用环境变量或密钥管理器。
- 生成的 `SKILL.md` 和 `memory.md` 可能包含个人信息，上传前请人工检查。

## 开发与验证

```bash
python3 -m py_compile ex_distill.py
python3 ex_distill.py --help
```

使用本地样本做完整统计流程时，请显式使用占位输出目录：

```bash
python3 ex_distill.py \
  --input /path/to/chat.json \
  --me '你的昵称' \
  --name smoke-test \
  --out /tmp/xpskill-test \
  --no-llm

unzip -l /tmp/xpskill-test/smoke-test.zip
```

项目当前未检测到独立测试套件；上述命令覆盖语法、参数解析和无 LLM 打包路径。

## 贡献

欢迎提交 Issue 或 Pull Request。请在提交前：

- [ ] 运行 `python3 -m py_compile ex_distill.py`
- [ ] 使用脱敏或合成聊天数据验证解析行为
- [ ] 不提交聊天记录、API Key 或生成的人设文件
- [ ] 更新与行为变更相关的文档

## 致谢

感谢以下开源项目：

- [agenmod/immortal-skill](https://github.com/agenmod/immortal-skill)：提供通用数字分身、角色模板、分维度蒸馏和证据意识等思路。xpskill 在此基础上，结合 xinpai-bot 的设备工作流进行了轻量化实现。
- [shuakami/qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter)：提供 QQ 聊天记录 JSON 导出能力。xpskill 使用其导出的结构化数据进行解析和蒸馏。

## 许可证

本项目以 [MIT License](LICENSE) 发布。
