#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""合并多个直播源为 TVBox live.txt（GitHub Actions 每日执行）"""
import glob
import re

GROUPS = {}
ORDER = []


def add_group(name):
    if name not in GROUPS:
        GROUPS[name] = []
        ORDER.append(name)


def parse_txt(path):
    cur = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                if "#genre#" in line:
                    cur = line.split("#")[0].strip().rstrip(",")
                    if cur:
                        add_group(cur)
                elif cur and "," in line:
                    name, url = line.split(",", 1)
                    name, url = name.strip(), url.strip()
                    if name and url and url.lower().startswith(("http", "rtmp", "rtsp")):
                        if (name, url) not in GROUPS[cur]:
                            GROUPS[cur].append((name, url))
    except Exception as e:
        print("parse_txt error:", path, e)


def parse_m3u(path):
    cur = None
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            if line.startswith("#EXTGRP:"):
                cur = line.split(":", 1)[1].strip()
                if cur:
                    add_group(cur)
            elif line.startswith("#EXTINF:"):
                name = ""
                m = re.search(r'group-title="([^"]*)"', line)
                if m:
                    cur = m.group(1).strip()
                m2 = re.search(r",([^,]+)$", line)
                if m2:
                    name = m2.group(1).strip()
                url = ""
                j = i + 1
                while j < len(lines) and not lines[j].strip().startswith("#"):
                    url = lines[j].strip()
                    break
                if name and url:
                    if not cur:
                        cur = "其他"
                        add_group(cur)
                    if (name, url) not in GROUPS[cur]:
                        GROUPS[cur].append((name, url))
            i += 1
    except Exception as e:
        print("parse_m3u error:", path, e)


def main():
    for p in sorted(glob.glob("raw/*")):
        if p.endswith(".m3u") or p.endswith(".m3u8"):
            parse_m3u(p)
        else:
            parse_txt(p)
    total = 0
    with open("live.txt", "w", encoding="utf-8") as f:
        for g in ORDER:
            f.write(g + ",#genre#\n")
            for name, url in GROUPS[g]:
                f.write("{},{}\n".format(name, url))
                total += 1
    print("total channels:", total)
    print("total groups:", len(ORDER))


if __name__ == "__main__":
    main()
