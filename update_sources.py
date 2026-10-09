#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fswws/tvbox-source 自动更新管线
================================
由 GitHub Actions 每 3 天触发一次（也可手动执行）。

逻辑：
  1. 从 sources.json 候选池依次拉取国内点播配置 → 清洗（去注释、只保留首个合法JSON、
     相对路径依赖改写成上游完整URL、GitHub raw 改走 fastly.jsdelivr）→ 校验 → 缓存到 vod/dom_N.json
  2. 从 jinenge(影视仓内置源) 中按关键词提取海外站点 → 生成 vod/oversea.json
  3. 拉取国内/海外直播源列表 → 校验非空 → 缓存到 live/dom_N.* / live/oversea_N.*
  4. 生成聚合配置 tvbox.json（单仓聚合：合并各源 sites，App 6.1.9 兼容）与直播配置 live.json，全部指向 fastly.jsdelivr 加速地址

用法：
  python update_sources.py                # 默认：国内点播5、国内直播5
  python update_sources.py --dom-vod 10 --dom-live 9   # 首次构建可放宽数量
  python update_sources.py --out <dir>    # 输出目录（默认脚本所在目录）
"""
import argparse
import copy
import json
import os
import re
import sys
import time
import urllib.request
from urllib.parse import urljoin

ROOT = os.path.dirname(os.path.abspath(__file__))
PROXY_PREFIX = "https://gh-proxy.com/"          # GitHub raw 的国内代理（备用通道）
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"


def _normalize_url(url):
    """非ASCII路径百分号编码 + IDN域名转punycode，确保 urllib 可请求。"""
    parts = urllib.parse.urlsplit(url)
    host = parts.hostname or ""
    if any(ord(c) > 127 for c in host):
        try:
            host = host.encode("idna").decode("ascii")
        except Exception:
            pass
        port = ""
        try:
            if parts.port is not None:
                port = f":{parts.port}"
        except Exception:
            pass
        netloc = host + port
        path = urllib.parse.quote(parts.path, safe="/%:@&=+$,;~*'()!?")
        q = parts.query
        if parts.username:
            auth = urllib.parse.quote(parts.username, safe="")
            if parts.password:
                auth += ":" + urllib.parse.quote(parts.password, safe="")
            netloc = auth + "@" + netloc
        url = urllib.parse.urlunsplit((parts.scheme, netloc, path, q, parts.fragment))
    else:
        url = urllib.parse.quote(url, safe=":/?#[]@!$&'()*+,;=%")
    return url


def fetch(url, timeout=30, retry=2):
    """拉取文本；非ASCII(中文/IDN)URL先规范化；直连失败自动经 gh-proxy.com 重试。"""
    last = None
    norm = _normalize_url(url)
    candidates = [norm]
    if "raw.githubusercontent.com" in url or "github.com" in url:
        candidates.append(PROXY_PREFIX + norm)
    for u in candidates:
        for _ in range(retry + 1):
            try:
                req = urllib.request.Request(u, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=timeout) as r:
                    data = r.read()
                return data.decode("utf-8", "replace")
            except Exception as e:
                last = e
                time.sleep(1)
    raise RuntimeError(f"fetch failed: {url} ({last})")


def strip_comments(text):
    """去掉 /* */ 块注释与行首 // 注释（保留 JSON 字符串内的 //）。"""
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    lines = []
    for ln in text.splitlines():
        if re.match(r"^\s*//", ln):
            continue
        lines.append(ln)
    return "\n".join(lines)


def extract_first_json(text):
    """提取第一个平衡的 JSON 对象（容忍前置注释/多个拼接对象）。"""
    t = strip_comments(text)
    start = t.find("{")
    if start < 0:
        return None
    depth = 0
    instr = False
    esc = False
    for i in range(start, len(t)):
        c = t[i]
        if instr:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                instr = False
        else:
            if c == '"':
                instr = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return t[start:i + 1]
    return None


def rewrite_github(u):
    """raw.githubusercontent / cdn.jsdelivr 统一改走 fastly.jsdelivr（国内可达）。"""
    m = re.match(r"^https://raw\.githubusercontent\.com/([^/]+)/([^/]+)/([^/]+)/(.+)$", u)
    if m:
        return f"https://gcore.jsdelivr.net/gh/{m.group(1)}/{m.group(2)}@{m.group(3)}/{m.group(4)}"
    m = re.match(r"^https://cdn\.jsdelivr\.net/gh/(.+)$", u)
    if m:
        return f"https://gcore.jsdelivr.net/gh/{m.group(1)}"
    return u


def resolve_rel(u, base):
    """相对路径 -> 基于配置URL的绝对路径；GitHub raw 再改写为 jsdelivr。"""
    if u and not u.startswith(("http://", "https://")):
        u = urljoin(base, u)
    return rewrite_github(u)


def parse_json_tolerant(obj_text):
    """容忍解析：去BOM、控制字符、多余尾逗号后重试 json.loads。"""
    t = obj_text.lstrip("\ufeff")
    for _ in range(3):
        try:
            return json.loads(t)
        except json.JSONDecodeError:
            pass
        # 去掉尾逗号（},  ]）再试
        t2 = re.sub(r",(\s*[}\]])", r"\1", t)
        if t2 == t:
            break
        t = t2
    # 最后：剥离JSON字符串外的非法控制字符再试一次
    t = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", t)
    return json.loads(t)


def clean_vod(raw_text, base_url):
    """清洗点播配置：取首个JSON对象、解析、改写相对依赖、重新序列化。"""
    obj = extract_first_json(raw_text)
    if obj is None:
        return None
    try:
        cfg = parse_json_tolerant(obj)
    except Exception:
        return None
    if not isinstance(cfg, dict) or not isinstance(cfg.get("sites"), list):
        return None
    if cfg.get("spider"):
        cfg["spider"] = resolve_rel(cfg["spider"], base_url)
    for site in cfg.get("sites", []):
        if not isinstance(site, dict):
            continue
        if site.get("api") and not site["api"].startswith(("http://", "https://", "csp_", "drpy")):
            site["api"] = resolve_rel(site["api"], base_url)
        for f in ("ext", "jar"):
            v = site.get(f)
            if isinstance(v, str) and v.startswith((".", "/")):
                site[f] = resolve_rel(v, base_url)
    parses = cfg.get("parses")
    if isinstance(parses, list):
        for p in parses:
            if isinstance(p, dict) and p.get("url") and not p["url"].startswith(("http://", "https://")):
                p["url"] = resolve_rel(p["url"], base_url)
    return cfg


def validate_live(text):
    """直播列表校验：足够大且含频道标记。"""
    if len(text) < 1000:
        return False
    if "#EXTINF" in text or "#genre#" in text or ".m3u8" in text.lower() or ".flv" in text.lower():
        return True
    return False


def pick_ext(url):
    m = re.search(r"\.(m3u|txt)$", url.split("?")[0], re.I)
    return m.group(1).lower() if m else "txt"


def trim_live(text, per_group=20):
    """直播列表瘦身：m3u 保留前 per_group 条；txt(#genre#) 每分组保留前 per_group 条。
    保证仓库与推送体积可控，同时列表仍然可用。"""
    lines = text.splitlines()
    if any(l.startswith("#EXTINF") for l in lines[:50]):
        out = [l for l in lines if l.startswith("#EXTM3U")]
        kept = 0
        i = 0
        n = len(lines)
        while i < n:
            l = lines[i]
            if l.startswith("#EXTINF") and kept < per_group:
                out.append(l)
                j = i + 1
                while j < n and not lines[j].strip():
                    j += 1
                while j < n and lines[j].startswith("#EXTVLCOPT"):
                    out.append(lines[j])
                    j += 1
                    while j < n and not lines[j].strip():
                        j += 1
                if j < n and not lines[j].startswith("#"):
                    out.append(lines[j])
                    i = j
                kept += 1
            i += 1
        return "\n".join(out) + "\n"
    out = []
    kept = 0
    for l in lines:
        if "#genre#" in l:
            kept = 0
            out.append(l)
        elif l.strip() and "," in l and kept < per_group:
            out.append(l)
            kept += 1
    return "\n".join(out) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dom-vod", type=int, default=None, help="国内点播保留数量")
    ap.add_argument("--dom-live", type=int, default=None, help="国内直播保留数量")
    ap.add_argument("--out", default=ROOT, help="输出目录")
    args = ap.parse_args()

    with open(os.path.join(ROOT, "sources.json"), encoding="utf-8") as f:
        pool = json.load(f)
    counts = pool["counts"]
    dom_vod_n = args.dom_vod or counts["domestic_vod"]
    dom_live_n = args.dom_live or counts["domestic_live"]
    jsd = pool["cdns"]["jsdelivr_base"]
    out = args.out
    os.makedirs(os.path.join(out, "vod"), exist_ok=True)
    os.makedirs(os.path.join(out, "live"), exist_ok=True)

    report = {"time": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
              "domestic_vod": [], "oversea_vod": [], "domestic_live": [], "oversea_live": []}

    # ---------- 1. 国内点播 ----------
    dom_vod_ok = []
    for cand in pool["domestic_vod"]:
        try:
            raw = fetch(cand["url"])
            cfg = clean_vod(raw, cand["url"])
            if cfg is None or len(cfg["sites"]) < 3:
                print(f"[dom-vod] 跳过 {cand['name']}: 解析失败或站点过少")
                continue
            usable = [s for s in cfg["sites"] if isinstance(s, dict) and s.get("api")]
            if len(usable) < 3:
                print(f"[dom-vod] 跳过 {cand['name']}: 可用站点 < 3")
                continue
            dom_vod_ok.append((cand["name"], cfg, len(usable)))
            print(f"[dom-vod] OK {cand['name']} sites={len(cfg['sites'])} usable={len(usable)}")
        except Exception as e:
            print(f"[dom-vod] 失败 {cand['name']}: {e}")
    dom_vod_selected = dom_vod_ok[:dom_vod_n]
    for i, (name, cfg, usable) in enumerate(dom_vod_selected, 1):
        with open(os.path.join(out, "vod", f"dom_{i}.json"), "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False)
        report["domestic_vod"].append({"name": name, "file": f"vod/dom_{i}.json", "usable_sites": usable})

    # ---------- 2. 海外点播（从已抓取的点播配置中按关键词提取海外站点） ----------
    # 每个海外站点单独成仓，携带其来源配置的 spider/jar，保证独立可用
    oversea_keywords = pool["oversea_vod"]["site_name_keywords"]  # ["玩偶","至臻","外剧","日本","巴士"]
    oversea_matched = {}
    for cand, cfg, _ in dom_vod_ok:
        for kw in oversea_keywords:
            if kw in oversea_matched:
                continue
            hit = next((s for s in cfg["sites"]
                        if isinstance(s, dict) and kw in str(s.get("name", ""))), None)
            if hit:
                oversea_matched[kw] = (cand, cfg, hit)
                print(f"[oversea] 找到 {kw} -> {hit.get('name')} (来自 {cand})")
    for i, kw in enumerate(oversea_keywords, 1):
        item = oversea_matched.get(kw)
        if not item:
            print(f"[oversea] 未找到 {kw}")
            continue
        src_name, src_cfg, site = item
        one = {
            "spider": src_cfg.get("spider", ""),
            "wallpaper": src_cfg.get("wallpaper", ""),
            "sites": [site],
            "parses": src_cfg.get("parses", []),
            "lives": [],
        }
        with open(os.path.join(out, "vod", f"oversea_{i}.json"), "w", encoding="utf-8") as f:
            json.dump(one, f, ensure_ascii=False)
        report["oversea_vod"].append({"name": site.get("name"), "key": site.get("key"),
                                      "file": f"vod/oversea_{i}.json", "source": src_name})
        print(f"[oversea] 生成 vod/oversea_{i}.json: {site.get('name')}")

    # ---------- 3. 直播 ----------
    def do_live(cands, prefix, limit, rpt_key):
        ok = []
        for cand in cands:
            try:
                text = fetch(cand["url"])
                if not validate_live(text):
                    print(f"[{prefix}] 跳过 {cand['name']}: 内容校验不过")
                    continue
                ext = pick_ext(cand["url"])
                fname = f"{prefix}_{len(ok) + 1}.{ext}"
                text = trim_live(text)
                with open(os.path.join(out, "live", fname), "w", encoding="utf-8") as f:
                    f.write(text)
                ok.append({"name": cand["name"], "file": f"live/{fname}"})
                print(f"[{prefix}] OK {cand['name']} -> {fname} ({len(text)}B)")
            except Exception as e:
                print(f"[{prefix}] 失败 {cand['name']}: {e}")
        report[rpt_key] = ok[:limit]
        return ok[:limit]

    dom_live = do_live(pool["domestic_live"], "dom", dom_live_n, "domestic_live")
    ov_live = do_live(pool["oversea_live"], "oversea", 99, "oversea_live")

    # ---------- 4. 生成 tvbox.json（单仓聚合：App 6.1.9 不支持多仓数组，须合并 sites）与 live.json ----------
    def _merge_sites(paths, keep_only_direct=True):
        """合并各源 sites；keep_only_direct=True 时丢弃 csp_ 型站点
        （其依赖的外部 spider jar 在 jsdelivr 上 403 不可用，只保留
        标准 http/https api 与 drpy 脚本源）。"""
        seen, merged = set(), []
        for p in paths:
            fp = os.path.join(out, p)
            if not os.path.exists(fp):
                print(f"[agg] 缺失 {p}，跳过")
                continue
            with open(fp, encoding="utf-8-sig") as f:
                data = json.load(f)
            prefix = os.path.splitext(os.path.basename(p))[0].replace("dom_", "d").replace("oversea_", "o")
            for s in data.get("sites", []) if isinstance(data, dict) else []:
                if not isinstance(s, dict) or not s.get("name"):
                    continue
                api = s.get("api") or ""
                if keep_only_direct and api.startswith("csp_"):
                    continue
                key = s.get("key") or ""
                nk = f"{prefix}_{key}" if key else f"{prefix}_s{len(merged)}"
                if nk in seen:
                    continue
                seen.add(nk)
                ns = copy.deepcopy(s)
                ns["key"] = nk
                merged.append(ns)
        return merged

    agg_sites = _merge_sites([f"vod/dom_{i}.json" for i in range(1, len(report["domestic_vod"]) + 1)]
                             + [item["file"] for item in report["oversea_vod"]])
    # 排序：标准 http api 采集源优先（主页推荐用快源），drpy/脚本源靠后
    def _site_rank(s):
        api = s.get("api", "")
        if api.startswith("http") and ("drpy" in api or api.endswith(".js")):
            return 2
        if api.startswith("http"):
            return 1
        return 3
    agg_sites = sorted(agg_sites, key=_site_rank)
    agg_lives = []
    seen_live = set()
    for p in [f"vod/dom_{i}.json" for i in range(1, len(report["domestic_vod"]) + 1)]:
        fp = os.path.join(out, p)
        if not os.path.exists(fp):
            continue
        with open(fp, encoding="utf-8-sig") as f:
            data = json.load(f)
        for g in data.get("lives", []) if isinstance(data, dict) else []:
            gn = g.get("group") or g.get("name") or ""
            if gn and gn not in seen_live:
                seen_live.add(gn)
                agg_lives.append(copy.deepcopy(g))
    # 追加 live.json 的远程直播源（保持与单仓点播配置同源聚合，App 直播 Tab 可见）
    livejson_path = os.path.join(out, "live.json")
    if os.path.exists(livejson_path):
        with open(livejson_path, encoding="utf-8-sig") as f:
            livejson = json.load(f)
        for item in livejson.get("lives", []):
            nm = item.get("name") or item.get("group") or ""
            if nm and nm not in seen_live:
                seen_live.add(nm)
                agg_lives.append(copy.deepcopy(item))
    agg = {
        "spider": "https://gcore.jsdelivr.net/gh/jinenge/tvbox@main/lib/jinenge.jar;md5;1d7a5147033044a81f91d5f4a510f9ed",
        "wallpaper": "https://jinenge.us.kg/wallpaper/",
        "sites": agg_sites,
        "lives": agg_lives,
        "parses": [],
    }
    with open(os.path.join(out, "tvbox.json"), "w", encoding="utf-8") as f:
        json.dump(agg, f, ensure_ascii=False, separators=(",", ":"))
    print(f"[agg] 单仓聚合 tvbox.json: sites={len(agg_sites)} lives={len(agg_lives)}")

    lives = []
    for item in dom_live:
        lives.append({"name": f"🇨🇳{item['name']}", "url": f"{jsd}/{item['file']}"})
    for item in ov_live:
        lives.append({"name": f"🌍{item['name']}", "url": f"{jsd}/{item['file']}"})
    with open(os.path.join(out, "live.json"), "w", encoding="utf-8") as f:
        json.dump({"lives": lives}, f, ensure_ascii=False, indent=2)

    # ---------- 5. 汇总 ----------
    with open(os.path.join(out, "last_update.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("==== 汇总 ====")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print(f"聚合配置: {os.path.join(out, 'tvbox.json')}")
    print(f"直播配置: {os.path.join(out, 'live.json')}")


if __name__ == "__main__":
    main()
