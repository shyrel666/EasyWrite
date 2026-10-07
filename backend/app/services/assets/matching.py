"""
资料名称/证书名称与评分标准、章节要求之间的文本匹配（纯规则）。

- phrase_ratio：短语去掉通用词（认证、证书、资质…）后，原文命中为 1，否则按二字片段命中比例
- code_ok：短语带标准号（9001、27001…）或英文缩写（CMMI、PMP…）时，至少一个要原样出现在对方文本中，
  避免"管理体系认证"之类的通用词让 ISO9001 配上 ISO27001 的评分项
"""
import re
from typing import List, Tuple

# 只表示"证书/资质"类别、不能区分具体证书的通用词
GENERIC = re.compile(r"认证|证书|资质|资格|证明|有效期|复印件|的|及|与|和|等|[^\w]|_")
STANDARD_NO = re.compile(r"(?<!\d)\d{4,5}(?!\d)")
ACRONYM = re.compile(r"[A-Z]{3,}")
CODE_PREFIXES = {"ISO", "IEC", "GBT"}


def compact(text: str) -> str:
    return re.sub(r"\s+", "", text or "").upper()


def codes(text: str) -> set:
    """标准号与英文缩写；四位年份（19xx/20xx）不算"""
    t = compact(text)
    numbers = {n for n in STANDARD_NO.findall(t) if not re.fullmatch(r"(?:19|20)\d\d", n)}
    return numbers | (set(ACRONYM.findall(t)) - CODE_PREFIXES)


def code_ok(phrase: str, text: str) -> bool:
    found = codes(phrase)
    return not found or bool(found & codes(text))


def phrase_ratio(phrase: str, text: str) -> float:
    core = GENERIC.sub("", compact(phrase))
    target = compact(text)
    if len(core) < 2:
        return 0.0
    if core in target:
        return 1.0
    grams = {core[i:i + 2] for i in range(len(core) - 1)}
    return sum(1 for g in grams if g in target) / len(grams)


def best_match(phrases: List[str], text: str) -> Tuple[float, str]:
    """多个短语中与文本最匹配的一个：(得分, 短语)；编号不符的短语不参与"""
    best, label = 0.0, ""
    for p in phrases:
        if not p or not code_ok(p, text):
            continue
        r = phrase_ratio(p, text)
        if r > best:
            best, label = r, p
    return best, label
