#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清洗 vod/tvbox.json：去 // 注释行、只保留第一个合法 JSON 对象（TVBox 单段标准配置）"""
import json


def clean(path):
    raw = open(path, encoding="utf-8").read()
    lines = raw.splitlines(keepends=True)
    buf = "".join(l for l in lines if not l.lstrip().startswith("//"))
    start = buf.find("{")
    depth = 0
    in_str = False
    esc = False
    end = -1
    for i in range(start, len(buf)):
        c = buf[i]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if end < 0:
        raise RuntimeError("未找到完整 JSON 对象")
    first = buf[start:end]
    obj = json.loads(first)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    print("vod/tvbox.json cleaned, sites:", len(obj.get("sites", [])), "lives:", len(obj.get("lives", [])))


if __name__ == "__main__":
    clean("vod/tvbox.json")
