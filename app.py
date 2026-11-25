# -*- coding: utf-8 -*-
"""رادار عروض الشاشات - يراجع المتاجر كل 3 ساعات. التشغيل: python app.py ثم افتح http://localhost:5000"""
import json, os, re, sqlite3, threading, time, webbrowser
from urllib.parse import quote, urljoin
import requests
from bs4 import BeautifulSoup
from flask import Flask, jsonify

INTERVAL_HOURS = 3
QUERIES = [("tv", "شاشة تلفزيون"), ("gaming", "شاشة ألعاب قيمنق")]
MAX_PER_SITE = 12
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/124 Safari/537.36",
           "Accept-Language": "ar-SA,ar;q=0.9,en;q=0.8"}

# عدّل الـ selectors هنا إذا غيّر المتجر تصميم صفحته
SITES = {
 "أمازون": dict(url="https://www.amazon.sa/s?k={q}", card="div[data-component-type='s-search-result']",
                title="h2", price=".a-price .a-offscreen", old=".a-price.a-text-price .a-offscreen", img="img.s-image", link="a.a-link-normal"),
 "نون": dict(url="https://www.noon.com/saudi-ar/search/?q={q}", card="div[data-qa^='product-']",
             title="div[data-qa='product-name']", price="strong.amount", old="span.oldPrice", img="img", link="a"),
 "جرير": dict(url="https://www.jarir.com/sa-ar/catalogsearch/result/?q={q}", card="div.product-item",
              title=".product-item-link", price=".special-price .price, .price", old=".old-price .price", img="img", link="a.product-item-link"),
 "نيو إق": dict(url="https://www.neweq.com/ar/search?q={q}", card=".product-item, .product-card",
                title="h2, h3, .title", price=".price-new, .special-price, .price", old=".price-old, .old-price", img="img", link="a"),
 "إكسترا": dict(url="https://www.extra.com/ar-sa/search/?text={q}", card="div.product-tile, li.product-item",
                title=".product-name, h2, h3", price=".price, .special-price", old=".old-price, .was-price", img="img", link="a"),
}

DATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "TVRadar")
os.makedirs(DATA_DIR, exist_ok=True)
DB = sqlite3.connect(os.path.join(DATA_DIR, "deals.db"), check_same_thread=False)
DB.execute("""CREATE TABLE IF NOT EXISTS deals(url TEXT PRIMARY KEY, store TEXT, title TEXT, price REAL, old REAL,
              img TEXT, promo TEXT, short TEXT, specs TEXT, seen REAL)""")
DB.execute("CREATE TABLE IF NOT EXISTS meta(k TEXT PRIMARY KEY, v TEXT)")
try: DB.execute("ALTER TABLE deals ADD COLUMN kind TEXT")
except sqlite3.OperationalError: pass
LOCK = threading.Lock()

def num(t):
    if not t: return None
    t = t.translate(str.maketrans("٠١٢٣٤٥٦٧٨٩٫٬", "0123456789.,"))
    m = re.search(r"\d[\d,]*\.?\d*", t)
    return float(m.group().replace(",", "")) if m else None

def pick(el, sel, attr=None):
    n = el.select_one(sel)
    if not n: return None
    return n.get(attr) if attr else n.get_text(" ", strip=True)

CODE_RE = re.compile(r"(?:كود|كوبون|رمز|code|coupon)[^\w]{0,6}(?:الخصم|خصم|discount)?[\s:：]*([A-Za-z0-9]{4,15})", re.I)
COND_RE = re.compile(r"[^.\n]{0,60}(?:عند الدفع|بطاقة|بنك|الحد الأدنى|للأعضاء|عند شراء|تطبيق|الأولى|حصري|when you|minimum|with card)[^.\n]{0,60}", re.I)

def detail(url):
    """يجلب صفحة المنتج: المواصفات + الكود + شروط العرض"""
    try:
        html = requests.get(url, headers=HEADERS, timeout=20).text
    except Exception:
        return {}, "", []
    s = BeautifulSoup(html, "lxml")
    specs = {}
    for tr in s.select("table tr"):
        c = [x.get_text(" ", strip=True) for x in tr.select("th,td")]
        if len(c) == 2 and 1 < len(c[0]) < 40 and c[1]: specs[c[0]] = c[1]
    for li in s.select("li"):
        t = li.get_text(" ", strip=True)
        if ":" in t and len(t) < 120:
            k, v = t.split(":", 1)
            if 1 < len(k) < 30 and v.strip(): specs.setdefault(k.strip(), v.strip())
    text = s.get_text("\n", strip=True)
    codes = list(dict.fromkeys(CODE_RE.findall(text)))[:2]
    conds = list(dict.fromkeys(m.strip() for m in COND_RE.findall(text)))[:2]
    promo = []
    if codes: promo.append("🏷️ كود الخصم: " + " / ".join(codes))
    promo += ["⚠️ " + c for c in conds]
    return dict(list(specs.items())[:40]), "\n".join(promo), codes

def short_from(title, specs):
    keys = ["الحجم", "حجم الشاشة", "الدقة", "نوع الشاشة", "Screen Size", "Resolution", "Display Type", "Smart", "نظام التشغيل"]
    parts = [f"{k}: {v}" for k, v in specs.items() if any(x.lower() in k.lower() for x in keys)][:4]
    return " • ".join(parts) or title[:120]


BRANDS = {
 "Samsung | سامسونج": r"samsung|سامسونج|سامسونغ", "LG | إل جي": r"\blg\b|ال جي|إل جي|الجي",
 "Sony | سوني": r"sony|سوني", "TCL | تي سي إل": r"\btcl\b|تي سي ال|تي سي إل",
 "Hisense | هايسنس": r"hisense|هايسنس|هاي سنس", "Xiaomi | شاومي": r"xiaomi|شاومي|redmi",
 "Philips | فيليبس": r"philips|فيليبس|فيلبس", "Panasonic | باناسونيك": r"panasonic|باناسونيك",
 "Toshiba | توشيبا": r"toshiba|توشيبا", "Sharp | شارب": r"sharp|شارب", "Haier | هاير": r"haier|هاير",
 "Hitachi | هيتاشي": r"hitachi|هيتاشي", "Skyworth | سكاي وورث": r"skyworth|سكاي ?وورث",
 "Aiwa | أيوا": r"aiwa|ايوا|أيوا", "Nikai | نيكاي": r"nikai|نيكاي", "Telefunken | تليفونكن": r"telefunken|تليفونكن",
 "Midea | ميديا": r"midea|ميديا", "Huawei | هواوي": r"huawei|هواوي", "Dell | ديل": r"\bdell\b|ديل",
 "HP | إتش بي": r"\bhp\b|اتش بي|إتش بي", "Asus | أسوس": r"asus|ايسوس|أسوس", "Acer | أيسر": r"acer|ايسر|أيسر",
 "MSI | إم إس آي": r"\bmsi\b", "BenQ | بينكيو": r"benq|بينكيو", "AOC": r"\baoc\b", "Lenovo | لينوفو": r"lenovo|لينوفو",
 "ViewSonic | فيوسونيك": r"viewsonic", "Alienware | أليانوير": r"alienware|اليانوير", "Corsair | كورسير": r"corsair", "Razer | ريزر": r"razer", "Gigabyte | جيجابايت": r"gigabyte|جيجابايت",
}
PANELS = [("QD-OLED", r"qd-?oled"), ("OLED", r"(?<![a-z])oled|اوليد"), ("Mini LED", r"mini[\s-]?led|ميني ?ليد"),
          ("Neo QLED", r"neo\s?qled"), ("QNED", r"qned"), ("QLED", r"qled|كيو ?ليد"), ("NanoCell", r"nano\s?cell|نانو ?سيل"),
          ("IPS", r"ips"), ("VA", r"(?<![a-z])va(?![a-z])"), ("TN", r"(?<![a-z])tn(?![a-z])"), ("LED", r"(?<![a-z])led|ليد"), ("LCD", r"lcd")]
RES = [("8K", r"\b8k\b|7680"), ("4K UHD", r"\b4k\b|uhd|2160|3840|الترا|ألترا"), ("QHD / 2K", r"qhd|1440|\b2k\b"),
       ("Full HD", r"full\s?hd|fhd|1080|فل ?اتش|فول ?اتش|فل ?إتش"), ("HD", r"\bhd\b|720|1366|اتش دي")]
OS = [("Google TV", r"google tv|جوجل تي"), ("Android TV", r"android|اندرويد|أندرويد"), ("webOS", r"webos"), ("Tizen", r"tizen"),
      ("Roku TV", r"roku"), ("Fire TV", r"fire tv"), ("VIDAA", r"vidaa"), ("Smart (عام)", r"smart|سمارت|ذكية|ذكي")]
COLOR = [("HDR10+", r"hdr ?10\+"), ("HDR10", r"hdr ?10(?!\+)"), ("HDR (عام)", r"\bhdr\b"), ("Dolby Vision", r"dolby vision|دولبي فيجن"),
         ("HLG", r"\bhlg\b"), ("Quantum Dot", r"quantum dot|كوانتم"), ("نطاق ألوان واسع", r"wide color|wcg|نطاق ألوان|الوان واسعة"),
         ("تعتيم موضعي", r"local dimming|تعتيم|اعتام"), ("10-bit", r"10[\s-]?bit")]
FEATS = [("HDMI 2.1", r"hdmi\s*2\.1"), ("Dolby Atmos", r"atmos|اتموس|أتموس"), ("DTS", r"\bdts\b"), ("Wi-Fi", r"wi-?fi|واي ?فاي"),
         ("Bluetooth", r"bluetooth|بلوتوث"), ("Chromecast", r"chromecast|كروم ?كاست"), ("AirPlay", r"airplay"),
         ("مساعد صوتي", r"alexa|google assistant|siri|مساعد صوتي|اليكسا"), ("FreeSync / G-Sync / VRR", r"freesync|g-?sync|\bvrr\b"),
         ("ALLM / وضع الألعاب", r"allm|game mode|وضع الألعاب"), ("شاشة منحنية", r"curved|منحني"),
         ("إطار نحيف", r"frameless|bezel|بدون إطار|حواف نحيفة"), ("تثبيت جداري VESA", r"vesa|تثبيت جداري"), ("Ethernet", r"ethernet|lan\b")]

def _hits(rx_list, text, first=False):
    out = [n for n, rx in rx_list if re.search(rx, text, re.I)]
    return out[:1] if first else out

def attrs(title, specs):
    t = (title + " " + " ".join(f"{k} {v}" for k, v in specs.items())).lower()
    brand = [n for n, rx in BRANDS.items() if re.search(rx, title.lower())] or [n for n, rx in BRANDS.items() if re.search(rx, t)]
    size = None
    for src in (title, t):
        m = re.search(r"(\d{2,3})[-\s]*(?:بوصة|بوصه|انش|إنش|inch|in\b|\"|”|″|')", src, re.I)
        if m and 15 <= int(m.group(1)) <= 120: size = int(m.group(1)); break
    b = None if not size else ("حتى 24 بوصة" if size <= 24 else "25–27 بوصة" if size <= 27 else "28–32 بوصة" if size <= 32 else "33–43 بوصة" if size <= 43 else "44–50 بوصة" if size <= 50
         else "51–58 بوصة" if size <= 58 else "59–65 بوصة" if size <= 65 else "66–75 بوصة" if size <= 75 else "76 بوصة وأكثر")
    hz = sorted({int(x) for x in re.findall(r"(\d{2,3})\s*(?:hz|هرتز)", t, re.I) if 30 <= int(x) <= 540})
    os_ = _hits(OS, t, True)
    if os_ and os_[0].startswith("Smart") and len(_hits(OS, t)) > 1: os_ = [_hits(OS, t)[0]]
    return dict(brand=brand[:1], res=_hits(RES, t, True), size=size, sizeb=[b] if b else [], panel=_hits(PANELS, t, True),
                color=_hits(COLOR, t), hz=[f"{h} هرتز" for h in hz[-1:]], rt=[f"{x} ms" for x in sorted({m for m in re.findall(r"(\d(?:\.\d)?)\s*ms", t)}, key=float)[:1]], os=os_, feat=_hits(FEATS, t))

def scrape_site(store, cfg, query):
    found = []
    url = cfg["url"].format(q=quote(query))
    try:
        r = requests.get(url, headers=HEADERS, timeout=25)
        soup = BeautifulSoup(r.text, "lxml")
    except Exception as e:
        print(f"[{store}] فشل: {e}"); return found
    for c in soup.select(cfg["card"])[:MAX_PER_SITE * 2]:
        title, price = pick(c, cfg["title"]), num(pick(c, cfg["price"]))
        href = pick(c, cfg["link"], "href") or (c.find("a") or {}).get("href")
        if not (title and price and href): continue
        img = c.select_one(cfg["img"])
        img = (img.get("src") or img.get("data-src")) if img else ""
        old = num(pick(c, cfg["old"]))
        found.append(dict(store=store, title=title, price=price, old=old if old and old > price else None,
                          img=urljoin(url, img or ""), url=urljoin(url, href)))
    print(f"[{store}] {len(found)} منتج")
    return found[:MAX_PER_SITE]

def refresh():
    with LOCK:
        for store, cfg in SITES.items():
            for kind, query in QUERIES:
                for d in scrape_site(store, cfg, query):
                    specs, promo, _ = detail(d["url"]); time.sleep(1.5)
                    DB.execute("INSERT OR REPLACE INTO deals(url,store,title,price,old,img,promo,short,specs,seen,kind) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (d["url"], store, d["title"], d["price"], d["old"], d["img"], promo,
                         short_from(d["title"], specs), json.dumps(specs, ensure_ascii=False), time.time(), kind))
                DB.commit()
        DB.execute("INSERT OR REPLACE INTO meta VALUES('last',?)", (str(time.time()),)); DB.commit()

def loop():
    while True:
        try: refresh()
        except Exception as e: print("خطأ:", e)
        time.sleep(INTERVAL_HOURS * 3600)

app = Flask(__name__)

@app.get("/api/deals")
def deals():
    cur = DB.execute("SELECT url,store,title,price,old,img,promo,short,specs,COALESCE(kind,'tv') FROM deals ORDER BY (old-price)/COALESCE(old,price) DESC")
    out = [dict(zip("url store title price old img promo short specs kind".split(), r)) for r in cur]
    for o in out:
        o["specs"] = json.loads(o["specs"] or "{}")
        o["a"] = attrs(o["title"], o["specs"])
    last = DB.execute("SELECT v FROM meta WHERE k='last'").fetchone()
    return jsonify(deals=out, last=float(last[0]) if last else None, busy=LOCK.locked())

@app.post("/api/refresh")
def manual():
    last = DB.execute("SELECT v FROM meta WHERE k='last'").fetchone()
    if last and time.time() - float(last[0]) < 600: return jsonify(ok=False, wait=True)
    if not LOCK.locked(): threading.Thread(target=refresh, daemon=True).start()
    return jsonify(ok=True)

PAGE = r"""<!doctype html><html lang="ar" dir="rtl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>رادار الشاشات</title>
<link href="https://fonts.googleapis.com/css2?family=Cairo:wght@600;800&family=IBM+Plex+Sans+Arabic:wght@400;600&display=swap" rel="stylesheet">
<style>
:root{--bg:#f2f4f8;--ink:#0d1b3a;--mut:#5b6678;--card:#fff;--line:#dde2ea;--acc:#1d4ed8;--onacc:#fff;--hot:#c81e1e;--tag:#eaf0ff}
@media(prefers-color-scheme:dark){:root{--bg:#0b1220;--ink:#e6ecf8;--mut:#93a0b5;--card:#131c2e;--line:#243049;--acc:#6ea0ff;--onacc:#0b1220;--hot:#e5484d;--tag:#1a2742}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:400 15px/1.6 'IBM Plex Sans Arabic',system-ui,sans-serif}
h1,.p,.dc{font-family:Cairo,'IBM Plex Sans Arabic',sans-serif}
.top{position:sticky;top:0;z-index:5;background:var(--bg);border-bottom:1px solid var(--line);padding:10px 16px}
.top>div{max-width:1280px;margin:0 auto}.hd{display:flex;justify-content:space-between;align-items:center;gap:8px}
h1{font-size:22px;font-weight:800;margin:0;line-height:1.2}small{color:var(--mut)}
button,.btn{font:600 14px 'IBM Plex Sans Arabic',sans-serif;border:0;border-radius:10px;padding:9px 14px;background:var(--acc);color:var(--onacc);cursor:pointer;text-decoration:none;display:inline-block;text-align:center}
button:focus-visible,.chip:focus-visible,input:focus-visible,select:focus-visible{outline:2px solid var(--acc);outline-offset:2px}
.srch{display:flex;gap:8px;margin-top:10px}.srch input{flex:1;padding:11px 14px;border-radius:10px;border:1px solid var(--line);background:var(--card);color:var(--ink);font:inherit}
.tabs{display:flex;gap:6px;margin-top:10px}.tabs button,.bar button{background:var(--card);color:var(--ink);border:1px solid var(--line)}
.tabs button{flex:1}.tabs .on,.bar .on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.bar{display:flex;gap:6px;overflow-x:auto;margin-top:8px}.bar button{white-space:nowrap;padding:5px 12px;font-size:13px;border-radius:99px}
.lay{max-width:1280px;margin:0 auto;padding:12px 16px 40px;display:grid;grid-template-columns:minmax(0,1fr);gap:12px}
#fp{display:none;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px}#fp.open{display:block}
@media(min-width:900px){.lay{grid-template-columns:290px minmax(0,1fr);gap:16px}#fp{display:block;position:sticky;top:150px;align-self:start;max-height:calc(100vh - 170px);overflow:auto}#ft{display:none}}
.fg{margin-bottom:12px}.fg b{display:block;font-size:13px;margin-bottom:4px}.row{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}.row label{font-size:13px;display:flex;flex-direction:column;gap:2px;flex:1;min-width:110px}
.row input,.row select{padding:7px;border-radius:8px;border:1px solid var(--line);background:var(--bg);color:var(--ink);font:inherit}
.chip{display:inline-block;border:1px solid var(--line);border-radius:99px;padding:2px 10px;margin:0 0 4px 4px;font-size:13px;cursor:pointer}.chip.on{background:var(--acc);color:var(--onacc);border-color:var(--acc)}
#m{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:14px;margin-top:6px}
.c{background:var(--card);border:1px solid var(--line);border-radius:14px;overflow:hidden;cursor:pointer;display:flex;flex-direction:column}.c:hover{border-color:var(--acc)}
.im{position:relative;aspect-ratio:4/3;background:#fff}.im img{width:100%;height:100%;object-fit:contain;padding:10px}
.dc{position:absolute;top:10px;inset-inline-start:10px;background:var(--hot);color:#fff;font-weight:800;font-size:14px;padding:1px 10px;border-radius:8px}
.b{padding:12px;display:flex;flex-direction:column;gap:7px;flex:1}.meta{display:flex;justify-content:space-between;font-size:13px}.st{color:var(--acc);font-weight:600}.kd{color:var(--mut)}
.t{font-weight:600;display:-webkit-box;-webkit-line-clamp:2;-webkit-box-orient:vertical;overflow:hidden}
.p{font-size:25px;font-weight:800;line-height:1.1}.o{color:var(--mut);text-decoration:line-through;margin-inline-start:8px;font-size:14px}
.pm{background:var(--tag);border-radius:8px;padding:6px 10px;font-size:13px;white-space:pre-line}.sh{color:var(--mut);font-size:13px}
.pill{display:inline-block;background:var(--tag);color:var(--acc);border-radius:6px;padding:0 8px;font-size:12px;margin:0 0 3px 4px}
dialog{border:0;border-radius:16px;max-width:560px;width:94vw;background:var(--card);color:var(--ink);padding:0;max-height:90vh}
dialog::backdrop{background:rgba(0,0,0,.55)}.in{padding:16px;display:flex;flex-direction:column;gap:10px}
table{width:100%;border-collapse:collapse;font-size:14px}td{padding:6px 4px;border-bottom:1px solid var(--line);vertical-align:top}td:first-child{color:var(--mut);width:38%}
@media(prefers-reduced-motion:no-preference){.c{transition:border-color .15s}}
</style></head><body>
<div class="top"><div><div class="hd"><div><h1>رادار الشاشات</h1><small id="last"></small></div><button id="rf">حدّث الآن</button></div>
<div class="srch"><input id="q" type="search" placeholder="ابحث: ماركة، موديل، مواصفة…"><button id="ft">فلترة</button></div>
<div class="tabs" id="tabs"></div><div class="bar" id="bar"></div></div></div>
<div class="lay"><aside id="fp"></aside><section><small id="cnt"></small><main id="m"></main></section></div><dialog id="d"></dialog>
<script>
let kind='all',all=[],store="الكل",q="",maxp="",mind=0,sort="disc",open=false;
const FAC=[['brand','الماركة'],['res','جودة الشاشة'],['sizeb','حجم الشاشة'],['panel','نوع الشاشة'],['color','الألوان والتقنيات'],['hz','معدل التحديث'],['rt','زمن الاستجابة'],['os','النظام الذكي'],['feat','مزايا أخرى (كلها مطلوبة)']];
const sel={};FAC.forEach(f=>sel[f[0]]=new Set());
const $=i=>document.getElementById(i),fmt=n=>n.toLocaleString('ar-SA')+' ر.س';
const esc=s=>(s||'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const pc=d=>d.old?Math.round((1-d.price/d.old)*100):0;
const txt=d=>(d.title+' '+d.short+' '+Object.entries(d.specs).map(e=>e.join(' ')).join(' ')).toLowerCase();
const num=s=>parseInt((s.match(/\d+/)||[0])[0]);
async function load(){const r=await (await fetch('/api/deals')).json();all=r.deals;
 $('last').textContent=r.busy?'جاري التحديث…':r.last?'آخر تحديث: '+new Date(r.last*1000).toLocaleString('ar-SA')+' (تلقائي كل 3 ساعات)':'جاري أول جلب للبيانات…';
 render()}
function ok(d){if(kind!='all'&&d.kind!=kind)return false;if(store!='الكل'&&d.store!=store)return false;
 if(q&&!q.toLowerCase().split(/\s+/).every(w=>txt(d).includes(w)))return false;
 if(maxp&&d.price>+maxp)return false;if(pc(d)<mind)return false;
 for(const [k] of FAC){const s=sel[k];if(!s.size)continue;const v=d.a[k]||[];
  if(k=='feat'?![...s].every(x=>v.includes(x)):!v.some(x=>s.has(x)))return false}
 return true}
function render(){
 $('tabs').innerHTML=[['all','الكل'],['tv','تلفزيونات'],['gaming','شاشات ألعاب']].map(t=>`<button class="${t[0]==kind?'on':''}" onclick="kind='${t[0]}';render()">${t[1]}</button>`).join('');
 $('bar').innerHTML=['الكل',...new Set(all.map(d=>d.store))].map(s=>`<button class="${s==store?'on':''}" onclick="store='${s}';render()">${s}</button>`).join('');
 const n=FAC.reduce((a,f)=>a+sel[f[0]].size,0)+(maxp?1:0)+(mind?1:0);
 $('ft').textContent=n?`فلترة (${n})`:'فلترة';
 let h='<div class="row"><label>أعلى سعر<input id="mp" type="number" min="0" value="'+maxp+'" placeholder="مثلاً 2000"></label><label>أقل خصم<select id="md">'+[0,10,20,30,40,50].map(v=>`<option value="${v}" ${v==mind?'selected':''}>${v?v+'% وأكثر':'أي خصم'}</option>`).join('')+'</select></label><label>الترتيب<select id="so">'+[['disc','الأعلى خصماً'],['low','الأرخص'],['high','الأغلى'],['size','الأكبر حجماً']].map(o=>`<option value="${o[0]}" ${o[0]==sort?'selected':''}>${o[1]}</option>`).join('')+'</select></label></div>';
 for(const [k,label] of FAC){const c={};all.forEach(d=>(d.a[k]||[]).forEach(v=>c[v]=(c[v]||0)+1));
  const vs=Object.keys(c).sort((a,b)=>k=='sizeb'||k=='hz'||k=='rt'?num(a)-num(b):c[b]-c[a]);if(!vs.length)continue;
  h+=`<div class="fg"><b>${label}</b>`+vs.map(v=>`<span class="chip ${sel[k].has(v)?'on':''}" data-k="${k}" data-v="${esc(v)}">${esc(v)} (${c[v]})</span>`).join('')+'</div>'}
 h+='<button id="rs" style="background:var(--mut)">مسح الفلاتر</button>';
 $('fp').innerHTML=h;$('fp').className=open?'open':'';
 $('mp').onchange=e=>{maxp=e.target.value;render()};$('md').onchange=e=>{mind=+e.target.value;render()};$('so').onchange=e=>{sort=e.target.value;render()};
 $('rs').onclick=()=>{FAC.forEach(f=>sel[f[0]].clear());maxp='';mind=0;q='';$('q').value='';render()};
 $('fp').querySelectorAll('.chip').forEach(c=>c.onclick=()=>{const s=sel[c.dataset.k],v=c.dataset.v;s.has(v)?s.delete(v):s.add(v);render()});
 const l=all.filter(ok).sort((a,b)=>sort=='low'?a.price-b.price:sort=='high'?b.price-a.price:sort=='size'?(b.a.size||0)-(a.a.size||0):pc(b)-pc(a));
 $('cnt').textContent=`${l.length} من ${all.length} عرض`;
 $('m').innerHTML=l.length?l.map(d=>{const i=all.indexOf(d);
 return `<div class="c" onclick="show(${i})"><div class="im">${pc(d)?`<span class="dc">${pc(d)}%-</span>`:''}<img src="${esc(d.img)}" alt="" loading="lazy"></div><div class="b">
 <div class="meta"><span class="st">${d.store}</span><span class="kd">${d.kind=='gaming'?'شاشة ألعاب':'تلفزيون'}</span></div><div class="t">${esc(d.title)}</div>
 <div><span class="p">${fmt(d.price)}</span>${d.old?`<span class="o">${fmt(d.old)}</span>`:''}</div>${d.promo?`<div class="pm">${esc(d.promo)}</div>`:''}
 <div>${[d.a.size?d.a.size+' بوصة':'',...d.a.res,...d.a.panel,...d.a.hz].filter(Boolean).slice(0,4).map(x=>`<span class="pill">${esc(x)}</span>`).join('')}</div><div class="sh">${esc(d.short)}</div></div></div>`}).join(''):'<p style="padding:20px">ما فيه نتائج. جرّب تخفف الفلاتر أو اضغط «مسح الفلاتر».</p>'}
function show(i){const d=all[i],s=Object.entries(d.specs),a=d.a;
 const tags=[...a.brand,...a.res,...a.sizeb,...a.panel,...a.hz,...a.rt,...a.os,...a.color,...a.feat];
 $('d').innerHTML=`<div class="in"><img src="${esc(d.img)}" style="max-height:220px;object-fit:contain;background:#fff;border-radius:8px"><b>${esc(d.title)}</b>
 <div><span class="p">${fmt(d.price)}</span>${d.old?`<span class="o">${fmt(d.old)}</span>`:''}</div>${d.promo?`<div class="pm">${esc(d.promo)}</div>`:''}
 <div>${tags.map(t=>`<span class="chip">${esc(t)}</span>`).join('')}</div>
 <table>${s.length?s.map(([k,v])=>`<tr><td>${esc(k)}</td><td>${esc(v)}</td></tr>`).join(''):'<tr><td colspan=2>المواصفات الكاملة بصفحة المتجر.</td></tr>'}</table>
 <a class="btn" href="${esc(d.url)}" target="_blank" rel="noopener">اذهب إلى ${d.store}</a><button style="background:var(--mut)" onclick="$('d').close()">إغلاق</button></div>`;$('d').showModal()}
$('rf').onclick=async()=>{const r=await (await fetch('/api/refresh',{method:'POST'})).json();$('last').textContent=r.wait?'تم التحديث قبل أقل من 10 دقائق':'جاري التحديث…'};
$('q').oninput=e=>{q=e.target.value;render()};$('ft').onclick=()=>{open=!open;render()};
load();setInterval(load,60000);
</script></body></html>"""

@app.get("/")
def index(): return PAGE

if __name__ == "__main__":
    threading.Thread(target=loop, daemon=True).start()
    app.run(host="0.0.0.0", port=5000)
