# xpskill

把聊天记录蒸成一锅 xinpai-bot 能直接吃的人设技能包。

[![License: MIT](https://img.shields.io/badge/License-MIT-green.svg)](LICENSE)

它在电脑端完成解析、统计、样本筛选和可选的 LLM 分析；设备端只接收几 KB 的成品，不负责现场处理一整锅聊天记录。

> 文档里的“小明”“小红”和 `xiaohong` 都是占位符。请替换成你自己的昵称、聊天对象和技能名，别把示例当成户口本。

## 快速开始

### 1. 先验证导入

`--no-llm` 只做本地解析和统计，不联网、不消耗 API 额度：

下面的昵称是示例，请替换成导出文件里真实出现的说话者名称。

```bash
cd /home/jn/xinpai-bot

python3 tools/xpskill/ex_distill.py \
  --input tools/xpskill/68.json \
  --me '我' \
  --name xiaohong \
  --out /home/jn/xinpai-bot/tools/dist \
  --no-llm
```

### 2. 使用 LLM 生成人设

下面以 ModelScope 的 OpenAI 兼容接口为例：

```bash
export MODELSCOPE_API_KEY='你的 ModelScope Token'

python3 tools/xpskill/ex_distill.py \
  --input tools/xpskill/68.json \
  --me '我' \
  --name xiaohong \
  --desc '朋友，聊天风格直接，偶尔使用表情和网络用语' \
  --base-url 'https://api-inference.modelscope.cn/v1' \
  --model 'Qwen/Qwen3-235B-A22B' \
  --out /home/jn/xinpai-bot/tools/dist
```

### 3. 上传技能

生成的文件是：

```text
/home/jn/xinpai-bot/tools/dist/xiaohong/SKILL.md
/home/jn/xinpai-bot/tools/dist/xiaohong/references/memory.md
/home/jn/xinpai-bot/tools/dist/xiaohong.zip
```

可以在设备控制台上传 `xiaohong.zip`，然后在技能列表中将它设置为主技能。也可以在生成时直接上传：

```bash
python3 tools/xpskill/ex_distill.py \
  --input tools/xpskill/68.json \
  --me '小明' \
  --target '小红' \
  --name xiaohong \
  --out /home/jn/xinpai-bot/tools/dist \
  --device '192.168.137.103:8080'
```

## 工作流程

```text
聊天记录
   |
   v
解析消息 -> 统计画像 -> 按会话选样本 -> LLM 人格/记忆分析
                                      |
                                      v
                         SKILL.md + references/memory.md
                                      |
                                      v
                                  技能 zip
```

没有 API Key 或使用 `--no-llm` 时，流程会停在统计阶段，仍然会生成可编辑的技能骨架——没有厨师，至少先把食材摆盘。

## 支持的输入

| 类型 | 入口 | 说明 |
| --- | --- | --- |
| QQ JSON | `--input chat.json` | 推荐使用 QQ Chat Exporter 导出；支持 `messages`、`list`、`items`、`data` 等消息数组。 |
| CSV | `--input chat.csv` | 自动识别时间、发送者、正文和 `IsSender` 等常见列名。 |
| TXT | `--input chat.txt` | 支持 `昵称: 内容`、带时间的消息行和 QQ 消息管理器导出文本。 |
| HTML/MHT | `--input chat.html` | 先提取纯文本，再按聊天消息格式解析。 |
| 微信 SQLite | `--input EnMicroMsg.db` | 读取已解密数据库的 `MSG` 表；用 `--channel` 指定会话。 |
| 文件夹 | `--input exports/` | 递归读取其中的 `.txt`、`.csv`、`.json`、`.html`、`.htm`、`.mht` 文件。 |

### 推荐：使用 QQ Chat Exporter 导出 JSON

QQ 聊天记录建议先使用 [QQ Chat Exporter](https://github.com/shuakami/qq-chat-exporter) 导出 JSON，再交给 xpskill 蒸馏。这样可以保留时间、发送者和正文等结构化字段，解析结果比手工复制文本更稳定。

基本流程：

```text
QQ Chat Exporter
      |
      | 导出 JSON
      v
tools/xpskill/ex_distill.py
      |
      | 统计 / LLM 蒸馏 / 打包
      v
xinpai-bot 技能包
```

导出完成后，将 JSON 路径传给 `--input`：

```bash
python3 tools/xpskill/ex_distill.py \
  --input '/path/to/qq-export.json' \
  --me '你的昵称' \
  --target '聊天对象昵称' \
  --name chat-persona \
  --out /home/jn/xinpai-bot/tools/dist \
  --no-llm
```

`--me` 和 `--target` 必须使用 JSON 中实际出现的发送者名称；不确定时可以先省略 `--target`，让脚本根据消息数量自动选择对象。QQ Chat Exporter 的安装和导出步骤请以其项目说明为准。

### JSON 示例

脚本会自动寻找常见字段：

```json
{
  "messages": [
    {
      "timestamp": "2026-09-24 14:30:00",
      "sender": {"name": "小红"},
      "content": "你好"
    }
  ]
}
```

消息正文支持 `text`、`content`、`message`、`body` 等字段；发送者支持 `name`、`remark`、`nickname`、`uin` 等字段。

### 微信 SQLite 示例

`--channel` 对应数据库中 `MSG.StrTalker` 的值：

```bash
python3 tools/xpskill/ex_distill.py \
  --input '/path/to/EnMicroMsg.db' \
  --channel '张三' \
  --me '我' \
  --target '张三' \
  --name zhangsan
```

## LLM 配置

脚本调用 OpenAI 兼容的 `POST /chat/completions` 接口。推荐使用环境变量保存密钥：

```bash
export MODELSCOPE_API_KEY='你的 Token'
```

也可以在上述完整命令中追加 `--api-key "$MODELSCOPE_API_KEY"`；命令行参数会覆盖环境变量。

参数优先级如下：

| 配置 | 读取顺序 |
| --- | --- |
| API Key | `--api-key`，然后 `LLM_API_KEY`、`MODELSCOPE_API_KEY`、`DASHSCOPE_API_KEY`、`OPENAI_API_KEY` |
| 接口地址 | `--base-url`，然后 `LLM_BASE_URL`，最后使用 ModelScope 默认值 |
| 模型名 | `--model`，然后 `LLM_MODEL`，最后使用 `Qwen/Qwen3-235B-A22B` |

每个样本批次会分别请求人格分析和关系记忆分析。默认最多 3 批、总样本预算 30,000 字符；服务商限流时可缩小请求：

在完整命令末尾追加 `--llm-chars 12000 --llm-batches 1` 即可缩小请求。

调试接口而不真正发送请求：

在完整命令末尾追加 `--dry-run-llm`。

如果 LLM 返回限流、HTML 或无法解析的 JSON，脚本会保留本地统计并生成骨架，不会丢失已解析的消息。

## 参数速查

| 参数 | 默认值 | 用途 |
| --- | --- | --- |
| `--input` | 无，必填 | 聊天记录文件或目录。 |
| `--name` | 无，必填 | 技能目录名和 zip 名称。 |
| `--display` | `--name` | 设备中显示的技能名称。 |
| `--me` | `我` | 本人的昵称，可用逗号分隔多个名称。 |
| `--target` | 自动判断 | 要蒸馏的对象；省略时选择消息最多的非本人说话者。 |
| `--channel` | 无 | 微信 SQLite 的 `StrTalker` 会话名。 |
| `--desc` | 空 | 对关系、性格、MBTI 等背景的补充。 |
| `--out` | `/home/jn/xinpai-bot/tools/dist` | 输出目录。 |
| `--base-url` | ModelScope | OpenAI 兼容接口地址。 |
| `--model` | `Qwen/Qwen3-235B-A22B` | 模型名。 |
| `--llm-chars` | `30000` | LLM 样本字符预算。 |
| `--llm-batches` | `3` | 最多分析批次数。 |
| `--no-llm` | 关闭 | 只做本地统计。 |
| `--dry-run-llm` | 关闭 | 只打印请求信息。 |
| `--no-redact` | 关闭 | 关闭默认隐私脱敏。 |
| `--device` | 无 | 生成后直接上传到 `<设备IP>:8080`。 |

完整参数以本机脚本为准：

```bash
python3 tools/xpskill/ex_distill.py --help
```

## 输出文件

### `SKILL.md`

设备端的人设主技能，包含：

- 身份与背景
- 说话风格和口头禅
- 情感模式与关系行为
- 硬规则
- 典型例句

LLM 成功时这些内容由聊天样本归纳；统计模式下会留下可编辑的骨架和客观统计线索。模型负责提炼，不负责替你编造童年回忆。

### `references/memory.md`

关系记忆文件，包含：

- 关系时间线
- 一起去过的地方
- 内部玩笑
- 争吵模式
- 甜蜜瞬间
- 称呼与专属用语
- 消息量、时间跨度、深夜占比和高频短语统计

## 隐私与安全

- 不使用 LLM 时，聊天内容只在本机处理。
- 使用 LLM 时，只有抽取出的代表片段会发送到指定服务商。
- 默认会脱敏手机号、身份证、银行卡、邮箱和详细地址，但不能保证完全匿名。
- `--no-redact` 会发送未经脱敏的片段，只在确认风险可接受时使用。
- 不要把 API Key 写入 README、源代码或 Git 提交。
- 生成的技能文件可能包含个人信息，上传前请检查内容。

## 常见问题

### `--target` 不在记录里

昵称、备注、QQ 号和 UID 可能不是同一个字符串。先查看脚本输出的“说话者”列表，再使用完全一致的名称；也可以省略 `--target` 自动选择。

### 解析出了 0 条消息

确认输入是明文导出，并检查正文、发送者字段是否符合支持的格式。纯文本至少应使用 `昵称: 内容` 或带时间的消息头。

### LLM 报 429、HTML 或非 JSON

依次检查 API Key、模型名和接口地址。`--base-url` 应该是兼容接口根地址，例如：

```text
https://api-inference.modelscope.cn/v1
```

不要把服务商网页首页地址当作 API 地址。可以先用 `--no-llm` 验证导入，再降低 `--llm-chars` 和 `--llm-batches`。

### 只生成了统计骨架

这表示消息解析和打包成功，但没有得到可解析的 LLM JSON，或者主动使用了 `--no-llm`。检查配置后重新运行即可，统计结果仍会保存在 `references/memory.md`。

### 设备聊天显示 `fault`

这是设备 WebSocket 或网络连接状态，不等同于技能包解析失败。先在设备控制台确认网络、激活状态和连接服务，再测试不启用该技能时的文字对话。

## 本地自测

```bash
cd /home/jn/xinpai-bot

python3 -m py_compile tools/xpskill/ex_distill.py

python3 tools/xpskill/ex_distill.py \
  --input tools/xpskill/68.json \
  --me '我' \
  --name xiaohong-test \
  --out /tmp/xpskill-test \
  --no-llm

unzip -l /tmp/xpskill-test/xiaohong-test.zip
```

预期 zip 至少包含：

```text
SKILL.md
references/memory.md
```

## 我们的优势

xpskill 参考了通用数字分身项目的思路，但把重点收窄到“聊天记录快速变成 xinpai-bot 技能”这条路径：

- **设备闭环更短**：从 JSON 解析、统计、LLM 蒸馏到 zip 打包一条命令完成，可选直接上传设备。
- **更贴近 QQ/中文聊天**：针对 QQ Chat Exporter 的 JSON、中文昵称、备注、时间格式和常见导出文本做了适配。
- **低门槛运行**：只依赖 Python 标准库，不需要额外安装一堆采集器或服务。
- **没有 Key 也能先验货**：`--no-llm` 可离线统计消息量、深夜比例、口头禅和冲突线索，先确认导入质量再调用模型。
- **失败可降级**：LLM 限流或返回异常时仍保留统计结果，并生成可编辑的人设骨架。
- **默认保护隐私**：发给 LLM 前默认脱敏常见个人信息，且支持完全本地的统计模式。

它不是要替代通用数字分身框架，而是为 xinpai-bot 场景提供一条更短、更容易重复执行的生产流水线。

## 致谢

感谢以下开源项目：

- [agenmod/immortal-skill](https://github.com/agenmod/immortal-skill)：提供通用数字分身、角色模板、分维度蒸馏和证据意识等思路。xpskill 在此基础上结合 xinpai-bot 的设备工作流做了轻量化实现。
- [shuakami/qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter)：提供 QQ 聊天记录 JSON 导出能力。xpskill 使用其导出的结构化数据进行解析和蒸馏。

本项目不包含上述项目的代码；具体许可证和使用方式请以各自原项目为准。
