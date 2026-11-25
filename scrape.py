# -*- coding: utf-8 -*-
import json, os, sys, time
os.environ.setdefault("APPDATA", "/tmp")
import app

out, seen = [], set()
for store, cfg in app.SITES.items():
    for kind, query in app.QUERIES:
        for d in app.scrape_site(store, cfg, query):
            if d["url"] in seen: continue
            seen.add(d["url"])
            specs, promo, _ = app.detail(d["url"]); time.sleep(1.5)
            d.update(kind=kind, promo=promo, short=app.short_from(d["title"], specs), specs=specs, a=app.attrs(d["title"], specs))
            out.append(d)
if not out:
    print("ما جاء أي عرض، لن يتم تحديث الموقع"); sys.exit(1)
os.makedirs("site", exist_ok=True)
json.dump(dict(deals=out, last=time.time(), busy=False), open("site/deals.json", "w", encoding="utf-8"), ensure_ascii=False)
print(len(out), "عرض")
