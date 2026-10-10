"""Space-time analysis of RVD rents and prices (after Esri's space-time pattern mining: a space-time cube, Mann-Kendall trends,
emerging hot spot categories, time series clustering), on the monthly tables of rvd.parquet:
  1.1M / 1.2M domestic rents / prices by class (A-E) in 3 regions (Hong Kong, Kowloon, New Territories)
  2.1M / 2.2M office rents / prices by grade (A-C) in 7 office areas
  3.1M retail and 4.1M flatted factory rents and prices in the 3 regions
Locations are RVD's own areas (areas.parquet); 'Kowloon Bay / Kwun Tong' is drawn with the Kwun Tong district shape.

A. Per series (one place, one property class, one measure), against its own past (3 regions are too few places for a
   neighbourhood statistic):
   - each month's local z: the value against the mean and spread of the 36 months before it; significant when |z| >= 1.96
   - Esri's emerging hot spot categories applied to those significant months (new, consecutive, intensifying, persistent,
     diminishing, sporadic, oscillating, historical; for highs and lows), with a Mann-Kendall test on the z series
   - Mann-Kendall trend of the values over the whole series and over the last 36 months
   - time series clustering: each series' year-on-year change, z-normalised, grouped by shape (k-means, k = 4, fixed start;
     series covering under half the months get cluster -1)
B. Office rents only: Getis-Ord Gi* across the 7 office areas each month (neighbours = the 3 nearest area centres, the area
   itself included), then the same categories on each area's Gi* series. WEAK: 7 places are far below the ~30 Esri advises,
   so every B result carries weak = true and should be read as descriptive.
Writes spacetime.parquet (one row per series and month), spacetime.json (one summary per series), and for web maps
spacetime-map.json (every series on one month axis) and areas.geojson (the area shapes).
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

def local_z(v):
    """each value against the WINDOW values before it (needs 12 of them); NaN where not computable"""
    out = np.full(len(v), np.nan)
    for i in range(len(v)):
        w = v[max(0, i - WINDOW):i]; w = w[~np.isnan(w)]
        if len(w) >= 12 and not np.isnan(v[i]) and w.std() > 0: out[i] = (v[i] - w.mean()) / w.std()
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

def run(rvd_parquet, areas_geojsonl, out_dir):
    c = duckdb.connect()
    ids = ', '.join(f"'{t}'" for t in TABLES)
    rows = c.execute(f"""SELECT "table", series, period_start, value FROM '{rvd_parquet}' WHERE "table" IN ({ids}) ORDER BY "table", series, period_start""").fetchall()
    months = sorted({r[2] for r in rows}); mi = {m: i for i, m in enumerate(months)}
    cube = {}
    for t, s, p, v in rows:
        k = parse(t, s)
        if not k: continue
        cube.setdefault(k, np.full(len(months), np.nan))[mi[p]] = np.nan if v is None else v
    cube = {k: v for k, v in cube.items() if (~np.isnan(v)).sum() >= 24}
    # A: per series
    summary, out = [], []
    keys = sorted(cube)
    zs = {k: local_z(cube[k]) for k in keys}
    # clustering on the shape of the cycle: each series' year-on-year change, z-normalised over its own months, a missing month counts as the series' mean (0); series
    # covering under half the months are left out (cluster -1)
    growth = {k: np.concatenate([np.full(12, np.nan), cube[k][12:] / cube[k][:-12] - 1]) for k in keys}   # year-on-year change
    dense = [k for k in keys if (~np.isnan(growth[k])).mean() >= 0.5]
    X = np.nan_to_num(np.array([(growth[k] - np.nanmean(growth[k])) / (np.nanstd(growth[k]) or 1) for k in dense]))
    lab = dict(zip(dense, kmeans(X))) if len(dense) >= 4 else {}
    # B: office rents, Gi* across the 7 areas per grade
    shapes = {}
    for line in open(areas_geojsonl):
        f = json.loads(line); ring = np.array(f['geometry']['coordinates'][0] if f['geometry']['type'] == 'Polygon' else f['geometry']['coordinates'][0][0])
        shapes[f['properties']['area']] = ring[:, :2].mean(0)
    gi, b_cat = {}, {}
    for grade in ('Grade A', 'Grade B', 'Grade C'):
        ks = [k for k in keys if k[0] == 'office' and k[1] == 'rent' and k[2] == grade]
        if len(ks) < 4: continue
        cen = np.array([shapes[AREA_SHAPE.get(k[3], k[3])] for k in ks])
        d = ((cen[:, None] - cen[None]) ** 2).sum(2); nbrs = [list(np.argsort(d[i])[:3]) for i in range(len(ks))]
        g = np.array([gi_star(np.array([cube[k][t] for k in ks]), nbrs) for t in range(len(months))]).T
        for i, k in enumerate(ks): gi[k] = g[i]; b_cat[k] = category(g[i])
    for i, k in enumerate(keys):
        v, z = cube[k], zs[k]; prop, measure, cls, area = k; sid = f'{prop}|{measure}|{cls}|{area}'
        valid = np.flatnonzero(~np.isnan(v)); last = valid[-1]
        yoy = [None if j < 12 or np.isnan(v[j]) or np.isnan(v[j - 12]) or v[j - 12] == 0 else round(float(v[j] / v[j - 12] - 1), 4) for j in range(len(months))]
        s = {'series': sid, 'property': prop, 'measure': measure, 'unit': UNITS[measure], 'class_grade': cls, 'area': area, 'shape': AREA_SHAPE.get(area, area),
             'first': months[valid[0]].isoformat(), 'last': months[last].isoformat(), 'latest': float(v[last]), 'latest_yoy': yoy[last],
             'trend_all': mann_kendall(list(v)), 'trend_36m': mann_kendall(list(v[max(0, last - 35):last + 1])),
             'pattern': category(z), 'cluster': int(lab.get(k, -1)),
             'hotspot_b': {'pattern': b_cat[k], 'weak': True, 'places': 7} if k in b_cat else None}
        summary.append(s)
        for j in valid: out.append((sid, prop, measure, cls, area, months[j], float(v[j]), yoy[j], None if np.isnan(z[j]) else round(float(z[j]), 3),
                                    0 if np.isnan(z[j]) else int(z[j] >= Z) - int(z[j] <= -Z), None if k not in gi or np.isnan(gi[k][j]) else round(float(gi[k][j]), 3)))
    json.dump({'method': __doc__.split('Writes')[0].strip(), 'window_months': WINDOW, 'z': Z, 'months': [months[0].isoformat(), months[-1].isoformat()],
               'clusters': len(set(lab.values())), 'series': summary}, open(os.path.join(out_dir, 'spacetime.json'), 'w'), indent=1, ensure_ascii=False)
    # for web maps: every series' monthly values, local z and Gi* on one month axis, and the area shapes as GeoJSON
    r = lambda a: [None if np.isnan(x) else round(float(x), 3) for x in a]
    json.dump({'months': [m.isoformat()[:7] for m in months], 'series': [{**s, 'values': r(cube[k]), 'local_z': r(zs[k]), 'gi_z': r(gi[k]) if k in gi else None}
               for s, k in zip(summary, keys)]}, open(os.path.join(out_dir, 'spacetime-map.json'), 'w'), separators=(',', ':'), ensure_ascii=False)
    with open(os.path.join(out_dir, 'areas.geojson'), 'w') as f:
        json.dump({'type': 'FeatureCollection', 'features': [{'type': 'Feature', **json.loads(l)} for l in open(areas_geojsonl)]}, f, separators=(',', ':'), ensure_ascii=False)
    c.execute("""CREATE TABLE st (series VARCHAR, property VARCHAR, measure VARCHAR, class_grade VARCHAR, area VARCHAR, month DATE, value DOUBLE,
                 yoy DOUBLE, local_z DOUBLE, significant INTEGER, office_gi_z DOUBLE)""")
    c.executemany('INSERT INTO st VALUES (?,?,?,?,?,?,?,?,?,?,?)', out)
    p = os.path.join(out_dir, 'spacetime.parquet'); c.execute(f"COPY (SELECT * FROM st ORDER BY series, month) TO '{p}' (FORMAT parquet, COMPRESSION zstd)")
    return {'series': len(summary)}

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
