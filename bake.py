"""Hong Kong property market statistics from the Rating and Valuation Department (data.gov.hk
hk-rvd-tsinfo_rvd-property-market-statistics): every table the dataset lists, as one tidy table, plus the files themselves.
Writes, in data/out/:
  - rvd.parquet: one row per value of every English CSV table (rents, prices, indices, stock, completions, vacancy, take-up,
    transactions, demolitions, by month / quarter / year / district): table, title, the row's keys (time or district, and a
    second key where the table has one), series (the column), value (number, empty for '-', 'n/a', 'NA'), value_text (as
    published), remark (RVD's code in the column's '- Remarks' column, e.g. P, X, Z, S: see RVD's notes on its website),
    freq (M, Q, A or empty) and period_start (first day of the month, quarter or year)
  - raw/: every CSV and XLS file of the dataset, English and Chinese, exactly as downloaded
  - tables.json: each table's file, title, source URL and row count
  - areas.parquet: RVD's own area shapes from its CSDI layers (regions, the 18 districts, the office sub-districts), GeoParquet
    EPSG:4326, kept apart because CSDI data comes under the CSDI terms
  - spacetime.parquet, spacetime.json, spacetime-map.json, areas.geojson: the space-time analysis of rents and prices (spacetime.py)
  - index.json: counts, sizes, source
Usage: python3 bake.py"""
import csv, datetime, io, json, os, re, shutil, urllib.parse, urllib.request
import concurrent.futures as cf
import duckdb
import sources, spacetime

PACKAGE = 'https://data.gov.hk/en-data/api/3/action/package_show?id=hk-rvd-tsinfo_rvd-property-market-statistics'
CSDI = 'https://portal.csdi.gov.hk/server/rest/services/common'
AREA_LAYERS = {'domestic': 'rvd_rcd_1631695369038_14075', 'offices': 'rvd_rcd_1631762126297_43900'}   # regions + districts; + office sub-districts
NULLS = {'', '-', 'n/a', 'na', 'N/A', 'NA', '–'}

def get(u, tries=4):
    for attempt in range(tries):
        try: return urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'AlreadyOpen hk-property'}), timeout=120).read()
        except Exception:
            if attempt == tries - 1: raise

def fetch():
    """every CSV and XLS resource of the package into OUT/raw; returns [(file, url, format, name)]"""
    rs = [r for r in json.loads(get(PACKAGE))['result']['resources'] if r.get('format') in ('CSV', 'XLS')]
    raw = os.path.join(sources.OUT, 'raw'); shutil.rmtree(raw, ignore_errors=True); os.makedirs(raw)
    def one(r):
        u = r['url'].replace('http://', 'https://'); fn = urllib.parse.unquote(u.rsplit('/', 1)[-1])
        open(os.path.join(raw, fn), 'wb').write(get(urllib.parse.quote(u, safe=':/?=&%')))
        return fn, u, r['format'], r['name']
    with cf.ThreadPoolExecutor(4) as ex: return list(ex.map(one, rs))

def text(b):
    for enc in ('utf-8-sig', 'cp950', 'big5hkscs'):
        try: return b.decode(enc)
        except UnicodeDecodeError: pass
    return b.decode('utf-8', 'replace')

def number(v):
    v = v.strip().replace(',', '')
    if v in NULLS: return None
    try: return float(v)
    except ValueError: return None

def period(key_name, k):
    """(freq, first day) for a time key: 'MM-YYYY', 'MM-MM/YYYY', 'YYYY'"""
    k = k.strip(); n = key_name.strip().lower()
    if n == 'month' and (m := re.fullmatch(r'(\d\d)-(\d{4})', k)): return 'M', f'{m[2]}-{m[1]}-01'
    if n == 'quarter' and (m := re.fullmatch(r'(\d\d)-\d\d/(\d{4})', k)): return 'Q', f'{m[2]}-{m[1]}-01'
    if n in ('year', 'year-end') and re.fullmatch(r'\d{4}', k): return 'A', f'{k}-01-01'
    return None, None

def tidy(path):
    """one table -> (title, rows); the leading key columns are the first column plus any text-only columns after it"""
    rows = [r for r in csv.reader(io.StringIO(text(open(path, 'rb').read()))) if any(c.strip() for c in r)]
    title, head, data = rows[0][0].strip(), [h.strip() for h in rows[1]], rows[2:]
    col = lambda i: [r[i].strip() if i < len(r) else '' for r in data]
    keys, i = [0], 1
    while i < len(head):
        h = head[i]
        if h.endswith('- Remarks'): i += 1; continue                   # a key's own remark (e.g. S for a sub-district)
        if all(number(v) is None and v not in NULLS for v in col(i)) and any(col(i)): keys.append(i); i += 1; continue
        break
    key_rmk = {k: k + 1 for k in keys if k + 1 < len(head) and head[k + 1].endswith('- Remarks')}
    out = []
    for r in data:
        r = r + [''] * (len(head) - len(r)); k1 = r[keys[0]].strip(); k2 = r[keys[1]].strip() if len(keys) > 1 else None
        freq, start = period(head[keys[0]], k1)
        krm = ' '.join(r[key_rmk[k]].strip() for k in keys if k in key_rmk).strip() or None
        for j in range(keys[-1] + 1, len(head)):
            if head[j].endswith('- Remarks') or not head[j]: continue
            rmk = r[j + 1].strip() if j + 1 < len(head) and head[j + 1].endswith('- Remarks') else ''
            out.append({'key1_name': head[keys[0]], 'key1': k1, 'key2_name': head[keys[1]] if len(keys) > 1 else None, 'key2': k2, 'key_remark': krm,
                        'series': re.sub(r'\s+', ' ', head[j]), 'value': number(r[j]), 'value_text': r[j].strip(), 'remark': rmk or None, 'freq': freq, 'period_start': start})
    return title, out

def areas():
    """RVD's area polygons (both CSDI layers, deduplicated by name) as GeoJSON lines; returns the file"""
    p = os.path.join(sources.CACHE, 'areas.geojsonl'); os.makedirs(sources.CACHE, exist_ok=True); seen = set()
    with open(p, 'w') as f:
        for kind, ds in AREA_LAYERS.items():
            fc = json.loads(get(f'{CSDI}/{ds}/FeatureServer/0/query?where=1%3D1&outFields=District,District_TC,Shape__Area&outSR=4326&f=geojson'))
            for x in fc['features']:
                n = x['properties']['District'].strip()
                if n in seen: continue
                seen.add(n); f.write(json.dumps({'properties': {'area': n, 'area_tc': (x['properties'].get('District_TC') or '').strip(), 'layer': kind,
                                                                'area_m2': x['properties'].get('Shape__Area')}, 'geometry': x['geometry']}, ensure_ascii=False) + '\n')
    return p, len(seen)

def main():
    os.makedirs(sources.OUT, exist_ok=True); os.makedirs(sources.CACHE, exist_ok=True)
    files = fetch(); tables, rows = [], []
    for fn, url, fmt, name in sorted(files):
        if fmt != 'CSV' or name.endswith('(Traditional Chinese)'): continue   # the Chinese CSVs and the XLS files carry the same figures; kept in raw/
        title, rs = tidy(os.path.join(sources.OUT, 'raw', fn))
        table = fn.rsplit('.', 1)[0]
        tables.append({'table': table, 'file': f'raw/{fn}', 'title': title, 'name': name, 'url': url, 'rows': len(rs)})
        rows += [{'table': table, 'title': title, **r} for r in rs]
    tl = os.path.join(sources.CACHE, 'rvd.jsonl'); open(tl, 'w').write('\n'.join(json.dumps(r, ensure_ascii=False) for r in rows))
    c = duckdb.connect(); c.execute('INSTALL spatial; LOAD spatial')
    pq = os.path.join(sources.OUT, 'rvd.parquet')
    c.execute(f"""COPY (SELECT * REPLACE (TRY_CAST(period_start AS DATE) AS period_start) FROM read_json('{tl}', format = 'newline_delimited',
                  columns = {{'table': 'VARCHAR', title: 'VARCHAR', key1_name: 'VARCHAR', key1: 'VARCHAR', key2_name: 'VARCHAR', key2: 'VARCHAR', key_remark: 'VARCHAR',
                             series: 'VARCHAR', value: 'DOUBLE', value_text: 'VARCHAR', remark: 'VARCHAR', freq: 'VARCHAR', period_start: 'VARCHAR'}})
                  ORDER BY "table", period_start NULLS LAST, key1, key2, series) TO '{pq}.part' (FORMAT parquet, COMPRESSION zstd)""")
    os.replace(pq + '.part', pq)
    json.dump(tables, open(os.path.join(sources.OUT, 'tables.json'), 'w'), indent=1, ensure_ascii=False)
    ap, n_areas = areas(); apq = os.path.join(sources.OUT, 'areas.parquet')
    c.execute(f"""COPY (SELECT properties->>'area' AS area, properties->>'area_tc' AS area_tc, properties->>'layer' AS layer, TRY_CAST(properties->>'area_m2' AS DOUBLE) AS area_m2,
                  ST_GeomFromGeoJSON(geometry::VARCHAR) AS geometry FROM read_json('{ap}', format = 'newline_delimited', columns = {{properties: 'JSON', geometry: 'JSON'}}))
                  TO '{apq}.part' (FORMAT parquet, COMPRESSION zstd)""")
    os.replace(apq + '.part', apq)
    st = spacetime.run(pq, ap, sources.OUT)
    idx = {'built': datetime.datetime.now().isoformat(timespec='seconds'),
           'counts': {'tables': len(tables), 'values': len(rows), 'raw_files': len(files), 'areas': n_areas, 'spacetime_series': st['series']},
           'latest_month': c.execute(f"SELECT max(period_start) FROM '{pq}' WHERE freq = 'M'").fetchone()[0].isoformat(),
           'MB': {f: round(os.path.getsize(os.path.join(sources.OUT, f)) / 1e6, 2) for f in ('rvd.parquet', 'areas.parquet', 'spacetime.parquet', 'spacetime.json', 'spacetime-map.json', 'areas.geojson')},
           'source': 'Rating and Valuation Department, Property Market Statistics (data.gov.hk); area shapes from RVD layers on the CSDI Portal'}
    json.dump(idx, open(os.path.join(sources.OUT, 'index.json'), 'w'), indent=1)
    print(json.dumps(idx))

if __name__ == '__main__':
    main()
