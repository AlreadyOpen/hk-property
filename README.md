# hk-property

Hong Kong property market statistics from the Rating and Valuation Department: every table as one tidy Parquet, the original
files, RVD's area shapes, and a space-time analysis of rents and prices (`spacetime.py`). Baked weekly by GitHub Actions,
published to Hugging Face ([dataset](https://huggingface.co/datasets/alreadyopen/hk-property),
[bucket](https://huggingface.co/buckets/alreadyopen/hk-property)) and mirrored to
[Kaggle](https://www.kaggle.com/datasets/helenkwok/hk-property). Made from
[pipeline-template](https://github.com/AlreadyOpen/pipeline-template).

Run locally: `pip install -r requirements.txt && python spacetime.py && python bake.py`.

Code: Apache-2.0. Data: Rating and Valuation Department, under the [DATA.GOV.HK Terms and Conditions](https://data.gov.hk/en/terms-and-conditions)
(area shapes: [CSDI Terms of Use](https://portal.csdi.gov.hk/csdi-webpage/doc/TNC)).
