# -*- coding: utf-8 -*-
"""每3天自动更新体育直播源 live_sports.txt：抓 zby/zbefine + 新鲜源(alantang/junho/dashare)，
提取体育频道（重点 CCTV5/5+），测活后多线路输出。GitHub Actions 中执行。"""
import re, os, sys, time, urllib.request, tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(ROOT, "live_sports.txt")
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
SRC_URLS = {
    "zby": "https://myernestlu.github.io/zby.txt",
    "zbefine": "https://raw.githubusercontent.com/zbefine/iptv/main/iptv.m3u",
    "altang": "https://raw.githubusercontent.com/alantang1977/aTV/master/output/ipv4/result.txt",
    "junho": "https://gitee.com/junho1688/iptv-source/raw/master/cn2.txt",
    "dashare": "https://raw.githubusercontent.com/DataShare-duo/MovieLiveUrl/refs/heads/main/movie_live.txt",
}
SPORT_KEY = re.compile(r'(体育|sport|cctv5|cctv-5|cctv_5|竞赛|劲爆|五星|搏击|赛车|足球|篮球|赛事|ELEVEN|eleven|高尔夫|网球|乒羽|冰雪|棋牌|台球|钓鱼|NewTV-超级体育)')
EXCLUDE = {'5首劲爆dj', '篮球纪录片大全点播', '篮球纪录片点播TV', 'JJ斗地主'}

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

def parse_any(text):
    groups = {}; cur = '未分组'
    lines = text.splitlines(); i = 0
    while i < len(lines):
        line = lines[i].strip()
        if not line:
            i += 1; continue
        if line.startswith('#EXTINF'):
            m = re.search(r'group-title="([^"]*)"', line)
            if m and m.group(1):
                cur = m.group(1)
            name = line.rsplit(',', 1)[-1].strip() if ',' in line else ''
            groups.setdefault(cur, {}); groups[cur].setdefault(name, [])
            cur_ch = name
            i += 1
            if i < len(lines) and lines[i].strip().startswith('http'):
                groups[cur][cur_ch].append(lines[i].strip())
            i += 1
            continue
        if line.endswith('#genre#'):
            cur = line.rsplit(',', 1)[0].strip().rstrip(',')
            groups.setdefault(cur, {})
        elif ',' in line and not line.startswith('#'):
            name, urls = line.split(',', 1)
            ul = [u.strip() for u in urls.split('#') if u.strip().startswith('http')]
            if ul:
                groups.setdefault(cur, {}).setdefault(name.strip(), [])
                for u in ul:
                    if u not in groups[cur][name.strip()]:
                        groups[cur][name.strip()].append(u)
        i += 1
    return groups

def is_sport(n):
    return bool(SPORT_KEY.search(n))

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

def pick(urls, maxn):
    live = [u for u in urls if alive.get(u)]
    dead = [u for u in urls if not alive.get(u)]
    keep = []
    for u in live + dead:
        if u not in keep:
            keep.append(u)
        if len(keep) >= maxn:
            break
    return keep

alive = {}
def main():
    tmp = tempfile.gettempdir()
    texts = {}
    for k, url in SRC_URLS.items():
        try:
            texts[k] = fetch(url)
        except Exception as e:
            texts[k] = ''
            print(f"fetch {k} fail: {e}")
    if os.path.exists(os.path.join(ROOT, "live_all2.txt")):
        texts['old'] = read_any(os.path.join(ROOT, "live_all2.txt"))
    all_src = {}
    for k, text in texts.items():
        if not text:
            continue
        gs = parse_any(text)
        for g, chs in gs.items():
            for n, us in chs.items():
                if n in EXCLUDE:
                    continue
                if is_sport(n) or is_sport(g):
                    all_src.setdefault(n, [])
                    for u in us:
                        if u not in all_src[n]:
                            all_src[n].append(u)
    c5, c5p = [], []
    for k, text in texts.items():
        if not text:
            continue
        lines = text.splitlines()
        pend = None
        for line in lines:
            line = line.strip()
            if line.startswith('#EXTINF') and re.search(r'CCTV[-_]?5', line):
                pend = line
                continue
            if pend and line.startswith('http'):
                if 'rtp://' in line or 'udp://' in line:
                    pend = None
                    continue
                (c5p if re.search(r'CCTV[-_]?5\+', pend) else c5).append(line)
                pend = None
                continue
            if ',' in line and re.search(r'CCTV[-_]?5', line.split(',', 1)[0]):
                name, urls = line.split(',', 1)
                for u in urls.split('#'):
                    u = u.strip()
                    if not u.startswith('http') or 'rtp://' in u or 'udp://' in u:
                        continue
                    (c5p if re.search(r'CCTV[-_]?5\+', name) else c5).append(u)
        if pend:
            pass
    c5 = list(dict.fromkeys(c5)); c5p = list(dict.fromkeys(c5p))
    urls = []
    for us in all_src.values():
        for u in us:
            if u not in urls:
                urls.append(u)
    for u in c5 + c5p:
        if u not in urls:
            urls.append(u)
    with ThreadPoolExecutor(max_workers=40) as ex:
        futs = {ex.submit(test_url, u): u for u in urls}
        for f in as_completed(futs):
            u, ok = f.result()
            alive[u] = ok
    head = []
    c5l = pick(c5, 8); c5pl = pick(c5p, 8)
    if c5l:
        head.append(f"CCTV5体育,{'#'.join(c5l)}")
    if c5pl:
        head.append(f"CCTV5+体育赛事,{'#'.join(c5pl)}")
    skip = {'CCTV5','CCTV5体育','CCTV-5','CCTV5+','CCTV5+体育赛事','CCTV-5+','CCTV5+体育'}
    rest = []
    for n in sorted(all_src):
        if n in skip:
            continue
        keep = pick(all_src[n], 5)
        if keep:
            rest.append(f"{n},{'#'.join(keep)}")
    out = ['体育频道,#genre#'] + head + rest + ['']
    with open(OUT, 'w', encoding='utf-8', newline='\n') as f:
        f.write('\n'.join(out))
    print(f"channels={len(all_src)} urls={len(urls)} alive={sum(1 for v in alive.values() if v)} cctv5={len(c5l)} cctv5p={len(c5pl)} lines={len(out)}")
    sys.stdout.write("DONE\n")

if __name__ == '__main__':
    main()
