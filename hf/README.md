---
pretty_name: Hong Kong property market statistics (RVD)
license: other
license_name: data-gov-hk-terms-and-conditions
license_link: https://data.gov.hk/en/terms-and-conditions
tags:
  - hong-kong
  - property
  - real-estate
  - time-series
  - geospatial
size_categories:
  - 10K<n<100K
configs:
  - config_name: default
    data_files: rvd.parquet
---

# Hong Kong property market statistics (RVD)

Every table of the Rating and Valuation Department's Property Market Statistics, rebuilt weekly by
[AlreadyOpen/hk-property](https://github.com/AlreadyOpen/hk-property): rents, prices and indices (monthly, quarterly, annual,
from 1978-1999 depending on the table), stock, completions, forecast completions, vacancy, take-up, transactions and
demolitions, for domestic, office, retail, industrial and storage property.

| File | What |
|---|---|
| `rvd.parquet` | one row per value of every English CSV table: `table`, `title`, the row's keys (`key1`, `key2` with their names: a month, quarter, year or district), `series` (the column), `value` (number; empty for `-`, `n/a`), `value_text` (as published), `remark` (RVD's code from the column's remarks, e.g. P, X, Z, S, as published; RVD's notes on its website explain them), `freq` (M, Q, A) and `period_start` |
| `tables.json` | each table's file, title, source URL and row count |
| `raw/` | every CSV and XLS file of the dataset, English and Chinese, exactly as downloaded |
| `areas.parquet` | RVD's own area shapes (3 regions, 18 districts, 6 office sub-districts), GeoParquet EPSG:4326 |
| `spacetime.parquet`, `spacetime.json` | a space-time analysis of the monthly rents and prices (below) |

## Space-time analysis

After Esri's space-time pattern mining, on 82 monthly series (one place, one class or grade, one measure): domestic rents and
prices by class in 3 regions, office rents and prices by grade in 7 office areas, retail and flatted factory rents and prices
in 3 regions, January 1999 onwards.

- **Local z per month**: the month's value against the 36 months before it, significant at |z| >= 1.96.
- **Pattern per series**: Esri's emerging hot spot categories (new, consecutive, intensifying, persistent, diminishing,
  sporadic, oscillating, historical; hot or cold) applied to those months. With only 3 regions these compare a place with its
  own past, not with its neighbours.
- **Trend**: Mann-Kendall test over the whole series and the last 36 months.
- **Clusters**: series grouped by the shape of their year-on-year change (k-means, k = 4).
- **Office hot spots** (`office_gi_z`, `hotspot_b`): Getis-Ord Gi* across the 7 office areas each month. **Weak**: 7 places are
  far below the ~30 such statistics need; read them as descriptive.

The latest months are often provisional (remark P) and change when RVD revises them. Code and method: `spacetime.py`.

```python
import duckdb
duckdb.sql("""SELECT period_start, series, value FROM 'hf://datasets/alreadyopen/hk-property/rvd.parquet'
              WHERE "table" = '1.2M' AND series LIKE 'Class A%' ORDER BY period_start DESC LIMIT 6""")
```

For web maps, `spacetime-map.json` and `areas.geojson` are in the bucket
[alreadyopen/hk-property](https://huggingface.co/buckets/alreadyopen/hk-property). A copy is mirrored to
[Kaggle](https://www.kaggle.com/datasets/helenkwok/hk-property) after every bake.

## Source and terms

Data from the Government of the Hong Kong SAR, Rating and Valuation Department, via
[DATA.GOV.HK](https://data.gov.hk/en-data/dataset/hk-rvd-tsinfo_rvd-property-market-statistics), used under the
[DATA.GOV.HK Terms and Conditions](https://data.gov.hk/en/terms-and-conditions). The area shapes (`areas.parquet`,
`areas.geojson`) come from RVD's layers on the CSDI Portal, under the [CSDI Terms of Use](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC).
The Government owns the intellectual property rights in the data. This dataset is derived from it, provided as is with no
warranty, and not endorsed by the Government. The analysis is ours, not RVD's.
