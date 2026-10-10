"""Space-time analysis of Hong Kong property figures (after Esri's space-time pattern mining: a space-time cube, Mann-Kendall
trends, emerging hot spot categories, time series clustering), on three open sources:
  rvd  RVD's monthly rents and prices (rvd.parquet): domestic by class (A-E) in 3 regions, offices by grade (A-C) in 7 office
       areas, retail and flatted factories in the 3 regions; since 1999; drawn on RVD's own areas ('Kowloon Bay / Kwun Tong'
       with the Kwun Tong district shape)
  tps  Housing Authority Tenants Purchase Scheme sales (tps.parquet): each estate's median price per m2 by quarter, 39 estates,
       since 1998 (sales are sparse after 2020, so quarters, not months). Sales to sitting tenants at formula prices, not market
  lr   Land Registry agreements for sale and purchase of building units by its 10 districts (lr.parquet): monthly deals, value
       and average deal since 2021; drawn on RVD's district shapes ('Tsuen Wan' = Tsuen Wan + Kwai Tsing: our assumption)

Per series (one place, one measure):
  - local z each period: the value against the periods before it (36 months for rvd, 24 for lr, 12 quarters for tps),
    significant when |z| >= 1.96; Esri's emerging hot spot categories (new, consecutive, intensifying, persistent,
    diminishing, sporadic, oscillating, historical; hot or cold) on those significant periods: each place against its own past
  - Mann-Kendall trend over the whole series and over the recent window
  - time series clustering: each series' change on a year before, z-normalised, grouped by shape (k-means, k = 4, fixed
    start; series covering under half the periods get cluster -1)
Across places (Getis-Ord Gi* each period, then the same categories on each place's Gi* series): rvd office rents across the
7 office areas (3 nearest), lr across the 10 districts (3 nearest), tps across the 39 estates (8 nearest). Results from fewer
than 30 places carry weak = true: read them as descriptive.
Writes spacetime.parquet (one row per series and period), spacetime.json (one summary per series), and for web maps
spacetime-{rvd,tps,lr}.json (each source's series on its own period axis) and areas.geojson (the area shapes).
Self-check: python3 spacetime.py"""
import json, math, os, re
import duckdb
import numpy as np

WINDOW, Z = 36, 1.96
TABLES = {'1.1M': ('domestic', 'rent'), '1.2M': ('domestic', 'price'), '2.1M': ('office', 'rent'), '2.2M': ('office', 'price'),
          '3.1M': ('retail', None), '4.1M': ('flatted factory', None)}
AREA_SHAPE = {'Hong Kong': 'HONG KONG', 'Kowloon': 'KOWLOON', 'New Territories': 'NEW TERRITORIES', 'Kowloon Bay / Kwun Tong': 'Kwun Tong'}
UNITS = {'rent': 'HK$/m² per month', 'price': 'HK$/m²'}

def mann_kendall(x):
    """(tau, z, p) of the Mann-Kendall trend test on the non-missing values, ties corrected; None if fewer than 8 values"""
    x = np.asarray([v for v in x if v is not None and not math.isnan(v)], float); n = len(x)
    if n < 8: return None
    d = np.sign(x[None, :] - x[:, None])[np.triu_indices(n, 1)]; s = d.sum()
    _, t = np.unique(x, return_counts=True)
    var = (n * (n - 1) * (2 * n + 5) - (t * (t - 1) * (2 * t + 5)).sum()) / 18
    z = 0.0 if s == 0 else (s - np.sign(s)) / math.sqrt(var)
    return round(float(s / (n * (n - 1) / 2)), 4), round(float(z), 3), round(float(math.erfc(abs(z) / math.sqrt(2))), 5)

def local_z(v, window=WINDOW, least=12):
    """each value against the `window` values before it (needs `least` of them); NaN where not computable"""
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[max(0, i - window):i]; w = w[~np.isnan(w)]
        if len(w) >= least and not np.isnan(v[i]) and w.std() > 0: out[i] = (v[i] - w.mean()) / w.std()
    return out

def category(z):
    """Esri's emerging hot spot categories for one location's z series (hot = significant highs, cold = lows)"""
    zz = z[~np.isnan(z)]
    if len(zz) < 12: return 'not enough data'
    sig = np.where(zz >= Z, 1, np.where(zz <= -Z, -1, 0)); last = sig[-1]
    kind = 'hot' if last > 0 else 'cold'
    if last == 0:
        share = max((sig == 1).mean(), (sig == -1).mean())
        return f"historical {'hot' if (sig == 1).sum() >= (sig == -1).sum() else 'cold'} spot" if share >= 0.9 else 'no pattern'
    same = (sig == last); share = same.mean(); run = len(sig) - (np.flatnonzero(sig != last).max() + 1 if (sig != last).any() else 0)
    trend = mann_kendall(list(zz * last))                              # rising intensity in the direction of the spot
    if share >= 0.9:
        if trend and trend[2] < 0.05: return f"{'intensifying' if trend[1] > 0 else 'diminishing'} {kind} spot"
        return f'persistent {kind} spot'
    if (sig == -last).any(): return f'oscillating {kind} spot'           # Esri: a past spot of the other kind comes first
    if run == 1 and not same[:-1].any(): return f'new {kind} spot'
    if run >= 2 and not same[:len(sig) - run].any(): return f'consecutive {kind} spot'
    return f'sporadic {kind} spot'

def kmeans(X, k=4, iters=50):
    """k-means with a deterministic farthest-first start"""
    c = [X[0]]
    while len(c) < k: c.append(X[np.argmax(np.min([((X - ci) ** 2).sum(1) for ci in c], 0))])
    c = np.array(c)
    for _ in range(iters):
        lab = np.argmin(((X[:, None] - c[None]) ** 2).sum(2), 1)
        c = np.array([X[lab == j].mean(0) if (lab == j).any() else c[j] for j in range(k)])
    return lab

def gi_star(vals, nbrs):
    """Getis-Ord Gi* z for each place (binary weights, self included); vals may hold NaN (left out)"""
    ok = ~np.isnan(vals); n = ok.sum(); out = np.full(len(vals), np.nan)
    if n < 4: return out
    xb, s = vals[ok].mean(), vals[ok].std()
    if s == 0: return out
    for i in range(len(vals)):
        if not ok[i]: continue
        J = [j for j in nbrs[i] if ok[j]]; w = len(J)
        out[i] = (vals[J].sum() - xb * w) / (s * math.sqrt((n * w - w * w) / (n - 1)))
    return out

def parse(table, series):
    """(property, measure, class or grade, area) from a table id and a column name"""
    prop, measure = TABLES[table]
    if m := re.fullmatch(r'(Class [A-E]|Grade [ABC]) (.+)', series): return prop, measure, m[1], m[2].strip()
    if m := re.fullmatch(r'(Rents|Prices) \((.+)\)', series): return prop, m[1][:-1].lower(), 'all', m[2].strip()
    return None

def analyse(source, cube, periods, lag, window, least, meta, gi_groups=None, centre=None, k_nbrs=3):
    """the analysis of one source. cube: {key: values on `periods`}; lag: periods in a year; window/least: the local z window;
    meta(key) -> the series' descriptive fields; gi_groups(key) -> the group whose places are compared by Gi* (None: no Gi*);
    centre(key) -> (lon, lat) of its place. Returns (summaries, rows for spacetime.parquet, series for the map file)."""
    keys = sorted(cube); zs = {k: local_z(cube[k], window, least) for k in keys}
    growth = {k: np.concatenate([np.full(lag, np.nan), cube[k][lag:] / np.where(cube[k][:-lag] == 0, np.nan, cube[k][:-lag]) - 1]) for k in keys}
    dense = [k for k in keys if (~np.isnan(growth[k])).mean() >= 0.5]
    X = np.nan_to_num(np.array([(growth[k] - np.nanmean(growth[k])) / (np.nanstd(growth[k]) or 1) for k in dense])) if dense else None
    lab = dict(zip(dense, kmeans(X))) if len(dense) >= 4 else {}
    gi, b_cat, places = {}, {}, {}
    if gi_groups:
        groups = {}
        for k in keys:
            g = gi_groups(k)
            if g is not None: groups.setdefault(g, []).append(k)
        for g, ks in groups.items():
            if len(ks) < 4: continue
            cen = np.array([centre(k) for k in ks]) * [math.cos(math.radians(22.3)), 1]   # lon scaled to km-like units at HK
            d = ((cen[:, None] - cen[None]) ** 2).sum(2); nbrs = [list(np.argsort(d[i])[:min(k_nbrs, len(ks))]) for i in range(len(ks))]
            m = np.array([gi_star(np.array([cube[k][t] for k in ks]), nbrs) for t in range(len(periods))]).T
            for i, k in enumerate(ks): gi[k] = m[i]; b_cat[k] = category(m[i]); places[k] = len(ks)
    summary, out, mapped = [], [], []
    r3 = lambda a: [None if np.isnan(x) else round(float(x), 3) for x in a]
    for k in keys:
        v, z = cube[k], zs[k]; valid = np.flatnonzero(~np.isnan(v)); last = valid[-1]; md = meta(k); sid = f"{source}|{md['series']}"
        yoy = [None if j < lag or np.isnan(v[j]) or np.isnan(v[j - lag]) or v[j - lag] == 0 else round(float(v[j] / v[j - lag] - 1), 4) for j in range(len(periods))]
        s = {**md, 'series': sid, 'source': source, 'first': periods[valid[0]].isoformat(), 'last': periods[last].isoformat(), 'latest': float(v[last]), 'latest_yoy': yoy[last],
             'trend_all': mann_kendall(list(v)), 'trend_recent': mann_kendall(list(v[max(0, last - window + 1):last + 1])),
             'pattern': category(z), 'cluster': int(lab.get(k, -1)),
             'hotspot': {'pattern': b_cat[k], 'places': places[k], 'weak': places[k] < 30} if k in b_cat else None}
        summary.append(s); mapped.append({**s, 'values': r3(v), 'local_z': r3(z), 'gi_z': r3(gi[k]) if k in gi else None})
        for j in valid: out.append((source, sid, md.get('place'), periods[j], float(v[j]), yoy[j], None if np.isnan(z[j]) else round(float(z[j]), 3),
                                    0 if np.isnan(z[j]) else int(z[j] >= Z) - int(z[j] <= -Z), None if k not in gi or np.isnan(gi[k][j]) else round(float(gi[k][j]), 3)))
    return summary, out, mapped

def shapes_of(areas_geojsonl):
    """{area name: (lon, lat) of its outer ring's mean}"""
    out = {}
    for line in open(areas_geojsonl):
        f = json.loads(line); g = f['geometry']; ring = np.array(g['coordinates'][0] if g['type'] == 'Polygon' else g['coordinates'][0][0])
        out[f['properties']['area']] = ring[:, :2].mean(0)
    return out

def rvd(rvd_parquet, cen):
    c = duckdb.connect(); ids = ', '.join(f"'{t}'" for t in TABLES)
    rows = c.execute(f"""SELECT "table", series, period_start, value FROM '{rvd_parquet}' WHERE "table" IN ({ids}) ORDER BY "table", series, period_start""").fetchall()
    months = sorted({r[2] for r in rows}); mi = {m: i for i, m in enumerate(months)}; cube = {}
    for t, s, p, v in rows:
        k = parse(t, s)
        if k: cube.setdefault(k, np.full(len(months), np.nan))[mi[p]] = np.nan if v is None else v
    cube = {k: v for k, v in cube.items() if (~np.isnan(v)).sum() >= 24}
    meta = lambda k: {'series': '|'.join(k), 'property': k[0], 'measure': k[1], 'unit': UNITS[k[1]], 'class_grade': k[2], 'place': k[3], 'shape': [AREA_SHAPE.get(k[3], k[3])]}
    return months, analyse('rvd', cube, months, 12, WINDOW, 12, meta,
                           gi_groups=lambda k: k[2] if k[0] == 'office' and k[1] == 'rent' else None, centre=lambda k: cen[AREA_SHAPE.get(k[3], k[3])])

def tps(tps_parquet):
    """HA Tenants Purchase Scheme sales: each estate's median price per m2 per quarter (sales are sparse after 2020)"""
    c = duckdb.connect()
    rows = c.execute(f"""SELECT estate_name, district_group, avg(lat), avg(lon), date_trunc('quarter', assignment_date) q, median(price_per_m2), count(*), median(discount_rate)
                         FROM '{tps_parquet}' WHERE price_per_m2 > 0 GROUP BY ALL ORDER BY 1, q""").fetchall()
    qs = sorted({r[4] for r in rows}); qi = {q: i for i, q in enumerate(qs)}; cube, info, n = {}, {}, {}
    for est, grp, lat, lon, q, ppm, cnt, disc in rows:
        cube.setdefault(est, np.full(len(qs), np.nan))[qi[q]] = ppm; info[est] = (grp, lon, lat); n[est] = n.get(est, 0) + cnt
    cube = {k: v for k, v in cube.items() if (~np.isnan(v)).sum() >= 16}
    meta = lambda k: {'series': k, 'property': 'TPS flats (sales to sitting tenants, formula prices)', 'measure': 'median price', 'unit': 'HK$/m² (saleable)',
                      'class_grade': 'all', 'place': k, 'district_group': info[k][0], 'lon': info[k][1], 'lat': info[k][2], 'sales': n[k]}
    return qs, analyse('tps', cube, qs, 4, 12, 8, meta, gi_groups=lambda k: 'estates', centre=lambda k: info[k][1:], k_nbrs=8)

LR_SHAPES = {'Hong Kong': ['HONG KONG'], 'Kowloon': ['KOWLOON'], 'Island': ['Islands'], 'North': ['North'], 'Sai Kung': ['Sai Kung'], 'Shatin': ['Sha Tin'],
             'Tai Po': ['Tai Po'], 'Tsuen Wan': ['Tsuen Wan', 'Kwai Tsing'], 'Tuen Mun': ['Tuen Mun'], 'Yuen Long': ['Yuen Long']}
LR_NOTE = {'Tsuen Wan': "drawn as Tsuen Wan + Kwai Tsing districts: our assumption, the Land Registry publishes no district shapes"}

def lr(lr_parquet, cen):
    """Land Registry: agreements for sale and purchase of building units by its 10 districts, monthly (count, value, average)"""
    c = duckdb.connect()
    rows = c.execute(f"""SELECT regexp_extract(description, 'Number of (.+) transactions for ASP building units', 1) d, period_start, units, consideration_hkd_m
                         FROM '{lr_parquet}' WHERE description LIKE 'Number of % transactions for ASP building units' ORDER BY 1, 2""").fetchall()
    ms = sorted({r[1] for r in rows}); mi = {m: i for i, m in enumerate(ms)}; cube = {}
    for d, p, u, v in rows:
        for meas, x in (('deals', u), ('value', v), ('average deal', v / u if u and v is not None else None)):
            cube.setdefault((d, meas), np.full(len(ms), np.nan))[mi[p]] = np.nan if x is None else x
    unit = {'deals': 'agreements a month', 'value': 'HK$ million a month', 'average deal': 'HK$ million per agreement'}
    meta = lambda k: {'series': f'{k[1]}|{k[0]}', 'property': 'building units (all; agreements for sale and purchase)', 'measure': k[1], 'unit': unit[k[1]], 'class_grade': 'all',
                      'place': k[0], 'shape': LR_SHAPES.get(k[0], [k[0]]), 'shape_note': LR_NOTE.get(k[0])}
    centre = lambda k: np.mean([cen[s] for s in LR_SHAPES.get(k[0], [k[0]])], 0)
    return ms, analyse('lr', cube, ms, 12, 24, 12, meta, gi_groups=lambda k: k[1], centre=centre)

def run(rvd_parquet, areas_geojsonl, out_dir, tps_parquet=None, lr_parquet=None):
    cen = shapes_of(areas_geojsonl); parts = {'rvd': rvd(rvd_parquet, cen)}
    if tps_parquet: parts['tps'] = tps(tps_parquet)
    if lr_parquet: parts['lr'] = lr(lr_parquet, cen)
    summary, rows = [], []
    for src, (periods, (s, o, mapped)) in parts.items():
        summary += s; rows += o
        json.dump({'source': src, 'periods': [p.isoformat()[:7] for p in periods], 'step': 'quarter' if src == 'tps' else 'month', 'series': mapped},
                  open(os.path.join(out_dir, f'spacetime-{src}.json'), 'w'), separators=(',', ':'), ensure_ascii=False)
    json.dump({'method': __doc__.split('Writes')[0].strip(), 'z': Z, 'sources': {k: [p[0][0].isoformat(), p[0][-1].isoformat()] for k, p in parts.items()}, 'series': summary},
              open(os.path.join(out_dir, 'spacetime.json'), 'w'), indent=1, ensure_ascii=False)
    with open(os.path.join(out_dir, 'areas.geojson'), 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': [{'type': 'Feature', **json.loads(l)} for l in open(areas_geojsonl)]}, f, separators=(',', ':'), ensure_ascii=False)
    c = duckdb.connect()
    c.execute("""CREATE TABLE st (source VARCHAR, series VARCHAR, place VARCHAR, period DATE, value DOUBLE, yoy DOUBLE, local_z DOUBLE, significant INTEGER, gi_z DOUBLE)""")
    c.executemany('INSERT INTO st VALUES (?,?,?,?,?,?,?,?,?)', rows)
    c.execute(f"COPY (SELECT * FROM st ORDER BY source, series, period) TO '{os.path.join(out_dir, 'spacetime.parquet')}' (FORMAT parquet, COMPRESSION zstd)")
    return {'series': len(summary), **{f'series_{k}': len(p[1][0]) for k, p in parts.items()}}

if __name__ == '__main__':
    # self-check on synthetic series: a clean rise is a significant upward trend; categories behave at the edges
    t = mann_kendall(list(range(40))); assert t[0] == 1 and t[2] < 1e-6, t
    assert mann_kendall([1, 2] * 10)[2] > 0.05
    z = np.zeros(60); z[-1] = 3; assert category(z) == 'new hot spot', category(z)
    z = np.full(60, 2.5); assert category(z) == 'persistent hot spot', category(z)
    z = np.linspace(2, 6, 60); assert category(z) == 'intensifying hot spot', category(z)
    z = np.zeros(60); z[-5:] = -3; assert category(z) == 'consecutive cold spot', category(z)
    z = np.zeros(60); z[10] = 3; z[-1] = -3; assert category(z) == 'oscillating cold spot', category(z)
    g = gi_star(np.array([10, 10, 10, 1, 1, 1, 1.0]), [[0, 1, 2], [0, 1, 2], [0, 1, 2], [3, 4, 5], [3, 4, 5], [4, 5, 6], [4, 5, 6]]); assert g[0] > 1.5 > g[4], g
    print('spacetime: self-check ok')
