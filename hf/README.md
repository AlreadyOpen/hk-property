---
pretty_name: Hong Kong property market statistics (RVD, HA TPS, Land Registry)
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

# Hong Kong property market statistics (RVD, Housing Authority TPS, Land Registry)

Every table of the Rating and Valuation Department's Property Market Statistics, rebuilt weekly by
[AlreadyOpen/hk-property](https://github.com/AlreadyOpen/hk-property): rents, prices and indices (monthly, quarterly, annual,
from 1978-1999 depending on the table), stock, completions, forecast completions, vacancy, take-up, transactions and
demolitions, for domestic, office, retail, industrial and storage property.

| File | What |
|---|---|
| `rvd.parquet` | one row per value of every English CSV table: `table`, `title`, the row's keys (`key1`, `key2` with their names: a month, quarter, year or district), `series` (the column), `value` (number; empty for `-`, `n/a`), `value_text` (as published), `remark` (RVD's code from the column's remarks, e.g. P, X, Z, S, as published; RVD's notes on its website explain them), `freq` (M, Q, A) and `period_start` |
| `tables.json` | each table's file, title, source URL and row count |
| `raw/` | every CSV and XLS file of the dataset, English and Chinese, exactly as downloaded |
| `tps.parquet` | every Housing Authority Tenants Purchase Scheme sale since 1998 (157k, 39 estates): estate, district group, position, price, saleable area (m², ft²), price per m² and ft², floor band, discount rate, date, credit level. **Sales to sitting tenants at formula prices, not market sales** |
| `lr.parquet` | every row of the Land Registry's monthly statistics since January 2021: deeds by type, agreements for sale and purchase by its 10 districts (counts and consideration), residential / non-residential, primary / secondary, price bands |
| `areas.parquet` | RVD's own area shapes (3 regions, 18 districts, 6 office sub-districts), GeoParquet EPSG:4326 |
| `spacetime.parquet`, `spacetime.json` | a space-time analysis of the monthly rents and prices (below) |

## Space-time analysis

After Esri's space-time pattern mining, on 151 series from three sources: RVD's monthly rents and prices (82 series, since
1999, 3 regions and 7 office areas), each Tenants Purchase Scheme estate's median price per m² by quarter (39 estates, since
1998), and the Land Registry's monthly deals, value and average deal by its 10 districts (30 series, since 2021).

- **Local z per period**: the value against the periods before it (36 months for RVD, 24 for the Land Registry, 12 quarters
  for TPS), significant at |z| >= 1.96.
- **Pattern per series**: Esri's emerging hot spot categories (new, consecutive, intensifying, persistent, diminishing,
  sporadic, oscillating, historical; hot or cold) applied to those periods: each place against its own past.
- **Trend**: Mann-Kendall test over the whole series and the recent window.
- **Clusters**: series grouped by the shape of their change on a year before (k-means, k = 4).
- **Hot spots across places** (`gi_z`, `hotspot`): Getis-Ord Gi* each period across the 39 TPS estates (8 nearest), the 10
  Land Registry districts and the 7 RVD office areas (3 nearest). Results from fewer than 30 places are marked **weak**: read
  them as descriptive.
- Land Registry districts have no published shapes: they are drawn on RVD's district shapes, and **Tsuen Wan is drawn as
  Tsuen Wan + Kwai Tsing, our assumption**.

The latest periods are often provisional (RVD remark P) or partial (a quarter in progress). Code and method: `spacetime.py`.

```python
import duckdb
duckdb.sql("""SELECT period_start, series, value FROM 'hf://datasets/alreadyopen/hk-property/rvd.parquet'
              WHERE "table" = '1.2M' AND series LIKE 'Class A%' ORDER BY period_start DESC LIMIT 6""")
```

For web maps, `spacetime-map.json` (RVD), `spacetime-tps.json`, `spacetime-lr.json` and `areas.geojson` are in the bucket
[alreadyopen/hk-property](https://huggingface.co/buckets/alreadyopen/hk-property). A copy is mirrored to
[Kaggle](https://www.kaggle.com/datasets/helenkwok/hk-property) after every bake.

## Source and terms

Data from the Government of the Hong Kong SAR: the Rating and Valuation Department
([property market statistics](https://data.gov.hk/en-data/dataset/hk-rvd-tsinfo_rvd-property-market-statistics)), the Housing
Authority ([TPS sale transactions](https://data.gov.hk/en-data/dataset/hk-housing-tpssale-tpssale)) and the Land Registry
([monthly statistics](https://data.gov.hk/en-data/dataset/hk-lr-data4-landreg)), via DATA.GOV.HK, used under the
[DATA.GOV.HK Terms and Conditions](https://data.gov.hk/en/terms-and-conditions). The area shapes (`areas.parquet`,
`areas.geojson`) come from RVD's layers on the CSDI Portal, under the [CSDI Terms of Use](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC).
The Government owns the intellectual property rights in the data. This dataset is derived from it, provided as is with no
warranty, and not endorsed by the Government. The analysis is ours, not the departments'.
