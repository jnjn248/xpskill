# xpskill

<div align="center"><img alt="Tests" src="https://img.shields.io/badge/tests-not_configured-lightgrey.svg"><img alt="License" src="https://img.shields.io/badge/license-MIT-green.svg"><img alt="Version" src="https://img.shields.io/badge/version-unreleased-lightgrey.svg"><img alt="Language" src="https://img.shields.io/badge/language-Python-3776AB.svg"></div>

<p align="center">🇺🇸 <a href="./README.en.md">English</a> | 🇨🇳 <a href="./README.md">简体中文</a></p>

A chat-persona Skill package generator that distills chat history from sources such as QQ into lightweight, portable `SKILL.md` files, relationship-memory files, and `.zip` packages. The generated packages can be used with tools that support the Skill format or similar skill-package mechanisms, with xinpai-bot as the current primary target (project coming soon).

> [!NOTE]
> Names, paths, and API keys in this document are placeholders. Replace them with your own values and review generated files for personal information before uploading.

## Contents

- [Features](#features)
- [Installation](#installation)
- [Quick Start](#quick-start)
- [Supported Inputs](#supported-inputs)
- [CLI Reference](#cli-reference)
- [LLM Configuration](#llm-configuration)
- [Outputs](#outputs)
- [Privacy and Security](#privacy-and-security)
- [Development and Validation](#development-and-validation)
- [Contributing](#contributing)
- [License](#license)

## Features

- Parse JSON, CSV, TXT, HTML/MHT, WeChat SQLite, and directories containing supported files.
- Compute local statistics including message counts, time span, late-night ratio, session starts, average message length, phrases, and conflict hints.
- Split records into sessions and prioritize late-night, conflict-heavy, and longer conversations for sampling.
- Optionally call an OpenAI-compatible API to distill persona and relationship memory separately.
- Produce an editable statistics skeleton even without an API key or when the LLM is rate-limited or returns an invalid response.
- Redact selected samples by default and optionally upload the generated zip to a device.

## Installation

The runtime is Python 3.9+ with the standard library only. No `pip install` or third-party dependencies are required.

```bash
cd /home/jn/Echo-Mate/tools/xpskill
python3 -m py_compile ex_distill.py
```

## Quick Start

### Local statistics only

`--no-llm` performs local parsing and statistics without network access or model usage:

```bash
python3 ex_distill.py \
  --input /path/to/chat.json \
  --me 'your name' \
  --target 'contact name' \
  --name chat-persona \
  --out /path/to/output \
  --no-llm
```

When `--target` is omitted, the script selects the most frequent speaker who is not listed in `--me`.

### LLM distillation

The script sends representative samples to an OpenAI-compatible `/chat/completions` endpoint. Keep the key in an environment variable:

```bash
export MODELSCOPE_API_KEY="<YOUR_API_KEY>"

python3 ex_distill.py \
  --input /path/to/chat.json \
  --me 'your name' \
  --target 'contact name' \
  --name chat-persona \
  --desc 'Additional relationship and speaking-style context' \
  --base-url 'https://api-inference.modelscope.cn/v1' \
  --model 'Qwen/Qwen3-235B-A22B' \
  --out /path/to/output
```

### Upload directly to a device

`--device` uploads the generated zip with `POST /api/skills/upload`:

```bash
python3 ex_distill.py \
  --input /path/to/chat.json \
  --me 'your name' \
  --target 'contact name' \
  --name chat-persona \
  --out /path/to/output \
  --device '<DEVICE_IP>:8080'
```

## Supported Inputs

| Type | Example | Parsing behavior |
| --- | --- | --- |
| JSON | `--input chat.json` | Looks for message arrays under `messages`, `list`, `items`, `posts`, `comments`, `statuses`, or `data`. |
| CSV | `--input chat.csv` | Detects common time, sender, body, and `IsSender` columns. |
| TXT | `--input chat.txt` | Supports `name: message`, timestamped lines, and QQ export text. |
| HTML/MHT | `--input chat.html` | Extracts plain text before applying chat parsers. |
| WeChat SQLite | `--input EnMicroMsg.db` | Reads the decrypted `MSG` table; use `--channel` to filter `StrTalker`. |
| Directory | `--input exports/` | Recursively reads `.txt`, `.csv`, `.json`, `.html`, `.htm`, and `.mht` files. |

JSON bodies can use fields such as `text`, `content`, `message`, or `body`; sender objects can use `name`, `remark`, `nickname`, or `uin`.

## CLI Reference

| Option | Default | Description |
| --- | --- | --- |
| `--input` | required | Chat-history file or directory. |
| `--name` | required | Skill directory and zip name. |
| `--display` | `--name` | Display name shown on the device. |
| `--me` | `我` | Your name in the records; comma-separated names are supported. |
| `--target` | auto-detected | Person to distill; omitted selects the most frequent non-self speaker. |
| `--channel` | none | WeChat SQLite `StrTalker` session name. |
| `--desc` | empty | Additional relationship, personality, or background context. |
| `--out` | `/home/jn/Echo-Mate/tools/dist` | Output directory; explicit paths are recommended. |
| `--llm-chars` | `30000` | Character budget for LLM samples. |
| `--llm-batches` | `3` | Maximum number of analysis batches. |
| `--base-url` | ModelScope-compatible URL | OpenAI-compatible API root. |
| `--model` | `Qwen/Qwen3-235B-A22B` | Model name. |
| `--api-key` | environment variables | API key; supports `LLM_API_KEY`, `MODELSCOPE_API_KEY`, `DASHSCOPE_API_KEY`, and `OPENAI_API_KEY`. |
| `--no-llm` | off | Only run local statistics and create a skeleton. |
| `--dry-run-llm` | off | Print request information without calling the API. |
| `--no-redact` | off | Disable default redaction of phone, ID, card, email, and detailed-address patterns. |
| `--device` | none | Upload after generation to `<device-ip>:8080`. |

Full help:

```bash
python3 ex_distill.py --help
```

## LLM Configuration

Configuration precedence is:

| Setting | Resolution order |
| --- | --- |
| API key | `--api-key` -> `LLM_API_KEY` -> `MODELSCOPE_API_KEY` -> `DASHSCOPE_API_KEY` -> `OPENAI_API_KEY` |
| Base URL | `--base-url` -> `LLM_BASE_URL` -> ModelScope default |
| Model | `--model` -> `LLM_MODEL` -> `Qwen/Qwen3-235B-A22B` |

Each sample batch makes one persona request and one relationship-memory request. Reduce request size when needed:

```bash
python3 ex_distill.py ... --llm-chars 12000 --llm-batches 1
```

Use `--dry-run-llm` to inspect the request URL and sample length without sending chat content.

## Outputs

Each run creates:

```text
<out>/<name>/SKILL.md
<out>/<name>/references/memory.md
<out>/<name>.zip
```

| File | Contents |
| --- | --- |
| `SKILL.md` | Persona skill: identity, speaking style, phrases, emotional patterns, relationship behavior, hard rules, and example utterances. |
| `references/memory.md` | Relationship timeline, shared places, inside jokes, conflict patterns, sweet moments, names, and statistics. |
| `<name>.zip` | A device-uploadable archive containing the two files above. |

Without an API key, with `--no-llm`, or after an LLM failure, the script still writes statistics and an editable skeleton.

## Privacy and Security

> [!WARNING]
> Chat history may contain highly sensitive personal information. Confirm authorization and provider policies before uploading data or calling an external LLM.

- In `--no-llm` mode, chat content stays on the local machine.
- By default, only selected representative samples are sent to the configured LLM, after redacting common phone, ID, card, email, and detailed-address patterns.
- `--no-redact` sends unredacted samples; use it only when the risk is understood and acceptable.
- Never put API keys in README files, source code, or Git commits; use environment variables or a secret manager.
- Generated `SKILL.md` and `memory.md` may contain personal information. Review them before upload.

## Development and Validation

```bash
python3 -m py_compile ex_distill.py
python3 ex_distill.py --help
```

For a complete local smoke test, use synthetic or redacted data and an explicit temporary output directory:

```bash
python3 ex_distill.py \
  --input /path/to/chat.json \
  --me 'your name' \
  --name smoke-test \
  --out /tmp/xpskill-test \
  --no-llm

unzip -l /tmp/xpskill-test/smoke-test.zip
```

No separate test suite was detected; these commands cover syntax, argument parsing, and the no-LLM packaging path.

## Contributing

Issues and pull requests are welcome. Before submitting:

- [ ] Run `python3 -m py_compile ex_distill.py`.
- [ ] Validate parser behavior with synthetic or redacted chat data.
- [ ] Do not commit chat history, API keys, or generated persona files.
- [ ] Update documentation for behavior changes.

## Acknowledgments

Thanks to the following open-source projects:

- [agenmod/immortal-skill](https://github.com/agenmod/immortal-skill): for ideas around general-purpose digital personas, character templates, dimension-based distillation, and evidence-aware modeling. xpskill builds on these ideas with a lightweight workflow tailored for xinpai-bot devices.
- [shuakami/qq-chat-exporter](https://github.com/shuakami/qq-chat-exporter): for exporting QQ chat history as structured JSON, which xpskill uses for parsing and distillation.

## License

Released under the [MIT License](LICENSE).
