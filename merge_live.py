# -*- coding: utf-8 -*-
"""每3天自动更新直播源：抓取 zby.txt + zbefine.m3u，测活后合并进 live_all2.txt（保留旧线路）
在 GitHub Actions (update-live.yml) 中执行。"""
import re, os, sys, time, urllib.request, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.abspath(__file__))
OLD = os.path.join(ROOT, "live_all2.txt")
OUT = os.path.join(ROOT, "live_all2.txt")
ZBY_URL = "https://myernestlu.github.io/zby.txt"
ZBF_URL = "https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")

def fetch(url, timeout=30):
    req = urllib.request.Request(url, headers={'User-Agent': UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode('utf-8', errors='replace')

def read_any(p):
    raw = open(p, 'rb').read()
    for enc in ('utf-8-sig', 'utf-8', 'gb18030'):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode('utf-8', errors='replace')

def parse_txt(text):
    groups = {}; cur = None
    lines = text.splitlines(); i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1; continue
        merged = line
        while i + 1 < len(lines):
            nxt = lines[i+1]
            if nxt.startswith((' ', '\t')) or merged.endswith('&') or merged.endswith('='):
                merged += nxt.strip(); i += 1
            else:
                break
        if merged.endswith('#genre#'):
            cur = merged.rsplit(',', 1)[0].strip().rstrip(',')
            groups.setdefault(cur, {})
        elif ',' in merged:
            name, urls = merged.split(',', 1)
            if cur is None:
                cur = '未分组'; groups.setdefault(cur, {})
            name = name.strip()
            if not name:
                i += 1; continue
            ul = [u.strip() for u in urls.split('#') if u.strip().startswith('http')]
            if ul:
                groups[cur].setdefault(name, [])
                for u in ul:
                    if u not in groups[cur][name]:
                        groups[cur][name].append(u)
        i += 1
    return groups

def parse_m3u(text):
    groups = {}; cur_group = '未分组'; cur_ch = None
    for line in text.splitlines():
        line = line.strip()
        if line.startswith('#EXTINF'):
            m = re.search(r'group-title="([^"]*)"', line)
            if m and m.group(1):
                cur_group = m.group(1)
            name = line.rsplit(',', 1)[-1].strip() if ',' in line else ''
            cur_ch = name
            groups.setdefault(cur_group, {})
            if name:
                groups[cur_group].setdefault(name, [])
        elif line.startswith('#'):
            continue
        elif line.startswith('http') and cur_ch:
            groups[cur_group].setdefault(cur_ch, [])
            if line not in groups[cur_group][cur_ch]:
                groups[cur_group][cur_ch].append(line)
    return groups

def norm_name(n):
    n2 = re.sub(r'[\s\-_\(\)（）]', '', n).lower()
    m = re.match(r'cctv(\d+)', n2)
    if m:
        return 'cctv' + m.group(1)
    return n2

PROV = {'湖南':'湖南频道','北京':'北京频道','福建':'福建频道','甘肃':'甘肃频道','广东':'广东频道',
        '广西':'广西频道','贵州':'贵州频道','海南':'海南频道','河北':'河北频道','河南':'河南频道',
        '黑龙江':'黑龙江频道','湖北':'湖北频道','吉林':'吉林频道','江苏':'江苏频道','江西':'江西频道',
        '辽宁':'辽宁频道','内蒙古':'内蒙频道','宁夏':'宁夏频道','青海':'青海频道','山东':'山东频道',
        '山西':'山西频道','陕西':'陕西频道','上海':'上海频道','四川':'四川频道','天津':'天津频道',
        '新疆':'新疆频道','云南':'云南频道','浙江':'浙江频道','重庆':'重庆频道','西藏':'西藏频道',
        '安徽':'安徽频道'}
def map_group(g):
    if g == '央视频道':
        return '央视频道'
    if g in ('各省卫视', '卫视', '卫视频道'):
        return '卫视频道'
    if g == '港澳台':
        return '港澳台'
    if g == '体育频道':
        return '体育频道'
    if g in ('NewTV', '蓝光影视'):
        return 'NewTV'
    if g in ('少儿频道', '儿童频道'):
        return '儿童频道'
    if g in PROV:
        return PROV[g]
    return g

def test_url(url):
    try:
        req = urllib.request.Request(url, headers={'User-Agent': UA, 'Accept': '*/*'})
        with urllib.request.urlopen(req, timeout=6) as r:
            code = r.status
            ct = r.headers.get('Content-Type', '') or ''
            cl = r.headers.get('Content-Length') or ''
            ok = code == 200 and ('mpegurl' in ct or 'octet-stream' in ct or
                                  ct.startswith('application/') or
                                  (cl and cl.strip() and int(cl) > 0))
            if not ok and code == 200:
                ok = True
            return url, ok
    except Exception:
        return url, False

def main():
    # 下载候选源
    tmp = tempfile.gettempdir()
    zby_path = os.path.join(tmp, "zby_auto.txt")
    zbf_path = os.path.join(tmp, "zbefine_auto.m3u")
    open(zby_path, 'w', encoding='utf-8').write(fetch(ZBY_URL))
    open(zbf_path, 'w', encoding='utf-8').write(fetch(ZBF_URL))
    old = parse_txt(read_any(OLD)) if os.path.exists(OLD) else {}
    zby = parse_txt(read_any(zby_path))
    zbf = parse_m3u(read_any(zbf_path))

    # 候选：zby 精选组 + zbefine 全部
    new_candidates = {}
    def add_cand(g, n, us):
        tg = map_group(g)
        nn = norm_name(n)
        new_candidates.setdefault(tg, {}).setdefault(nn, {})
        if n not in new_candidates[tg][nn]:
            new_candidates[tg][nn][n] = []
        for u in us:
            if u not in new_candidates[tg][nn][n]:
                new_candidates[tg][nn][n].append(u)
    zby_keep = {'央视频道','各省卫视','港澳台','体育频道','蓝光影视','少儿频道','NewTV'}
    for g, chs in zby.items():
        if g in zby_keep:
            for n, us in chs.items():
                add_cand(g, n, us)
    for g, chs in zbf.items():
        for n, us in chs.items():
            add_cand(g, n, us)

    all_urls = []
    for tg, chs in new_candidates.items():
        for nn, names in chs.items():
            for name, us in names.items():
                for u in us:
                    if u not in all_urls:
                        all_urls.append(u)
    alive = {}
    with ThreadPoolExecutor(max_workers=40) as ex:
        futs = {ex.submit(test_url, u): u for u in all_urls}
        for f in as_completed(futs):
            u, ok = f.result()
            alive[u] = ok

    # 组装：旧全保留 + 新活线路并入
    merged = {}
    def add_old(g, n, us):
        nn = norm_name(n)
        merged.setdefault(g, {}).setdefault(nn, {})
        if n not in merged[g][nn]:
            merged[g][nn][n] = []
        for u in us:
            if u not in merged[g][nn][n]:
                merged[g][nn][n].append(u)
    for g, chs in old.items():
        for n, us in chs.items():
            add_old(g, n, us)
    for tg, chs in new_candidates.items():
        for nn, names in chs.items():
            for name, us in names.items():
                lu = [u for u in us if alive.get(u)]
                if not lu:
                    continue
                if tg in merged and nn in merged[tg]:
                    for n0 in merged[tg][nn]:
                        for u in lu:
                            if u not in merged[tg][nn][n0]:
                                merged[tg][nn][n0].append(u)
                else:
                    merged.setdefault(tg, {}).setdefault(nn, {})[name] = lu

    out_lines = []
    for g in merged:
        chs = merged[g]
        g_lines = []
        for nn, names in chs.items():
            best = None; best_urls = []
            for name, us in names.items():
                if len(us) > len(best_urls):
                    best_urls = us; best = name
            if best and best_urls:
                seen, keep = set(), []
                for u in best_urls:
                    if u not in seen:
                        seen.add(u); keep.append(u)
                    if len(keep) >= 8:
                        break
                g_lines.append(f"{best},{'#'.join(keep)}")
        if g_lines:
            out_lines.append(f"{g},#genre#")
            out_lines.extend(g_lines)
            out_lines.append('')
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out_lines))
    n_alive = sum(1 for v in alive.values() if v)
    print(f"candidate={len(all_urls)} alive={n_alive} groups={len(merged)} lines={len(out_lines)}")
    sys.stdout.write("DONE\n")

if __name__ == '__main__':
    main()
