import re


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
