"""Shared hostname policy used by the dashboard and application workflow."""
from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlsplit


def normalize_rule(value: str) -> str:
    rule = value.strip()
    if len(rule) > 256:
        raise ValueError("白名单规则最长为 256 个字符")
    if rule.startswith("re:"):
        if not rule[3:]:
            raise ValueError("re: 后需要填写正则表达式")
        try:
            re.compile(rule[3:], re.IGNORECASE)
        except re.error as error:
            raise ValueError(f"白名单正则无效：{error}") from error
        return rule
    rule = rule.lower().rstrip(".")
    if rule and not re.fullmatch(r"[a-z0-9.*?-]+", rule):
        raise ValueError("请填写域名、* 通配规则或 re: 开头的正则，不要填写完整网址")
    return rule


def check_domain(url: str, rules: list[str]) -> dict:
    parsed = urlsplit(url if "://" in url else "https://" + url)
    host = (parsed.hostname or "").lower().rstrip(".")
    if parsed.scheme not in {"http", "https"} or not host or len(host) > 253:
        raise ValueError("请输入有效的 http(s) 网址或域名")
    matched = ""
    for value in rules:
        rule = normalize_rule(value)
        if not rule:
            continue
        matches = (re.fullmatch(rule[3:], host, re.IGNORECASE) is not None
                   if rule.startswith("re:") else fnmatch.fnmatchcase(host, rule))
        if matches:
            matched = rule
            break
    return {"hostname": host, "allowed": bool(matched), "matched_rule": matched}
