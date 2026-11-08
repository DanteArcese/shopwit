# Geographic data

## `us_zcta.csv.gz`

US ZIP Code Tabulation Area (ZCTA) centroids, used to place a visitor's saved
ZIP code on the map so in-store prices can be limited to nearby stores
(`catalog/geo.py`). Columns: `zip,lat,lon` (one row per ZCTA, sorted by ZIP,
33,791 rows).

- Source: US Census Bureau, 2025 Gazetteer Files, ZIP Code Tabulation Areas
  (national file).
- URL: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_Gaz_zcta_national.zip
  (server `Last-Modified: Wed, 10 Sep 2025 17:30:11 GMT`; zip SHA-256
  `51516a4283bab5cd2376eec75609ddc4b363a18297e8adeeaac7b03cf7c84dbe`).
- Downloaded: 2026-11-08.
- License: public domain. It is a work of the US federal government
  (17 U.S.C. § 105), so it carries no copyright restrictions.

Conversion: from `2025_Gaz_zcta_national.txt` (pipe-delimited) we keep
`GEOID` as `zip`, `INTPTLAT` as `lat` and `INTPTLONG` as `lon`, values copied
verbatim (no rounding), header and whitespace stripped, then gzip with mtime 0.
Every other column is dropped.

```python
import csv, gzip, io, zipfile

text = zipfile.ZipFile("2025_Gaz_zcta_national.zip").read("2025_Gaz_zcta_national.txt").decode()
rows = [{k.strip(): v.strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(text), delimiter="|")]
out = io.StringIO()
w = csv.writer(out, lineterminator="\n")
w.writerow(["zip", "lat", "lon"])
for r in sorted(rows, key=lambda r: r["GEOID"]):
    w.writerow([r["GEOID"], r["INTPTLAT"], r["INTPTLONG"]])
with open("us_zcta.csv.gz", "wb") as f, gzip.GzipFile(fileobj=f, mode="wb", mtime=0, filename="") as g:
    g.write(out.getvalue().encode())
```

A ZCTA is the Census Bureau's area approximation of a USPS ZIP code. Not every
ZIP code has one (PO-box-only and single-building ZIPs often do not); those
are reported as unknown, never approximated.
