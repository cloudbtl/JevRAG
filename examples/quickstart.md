# Quickstart

```bash
pip install -e ".[dev]"
pytest -q                                  # 6 tests, no network

export CLOUDBTL_API_BASE=https://acme.cloudbtl.com
export CLOUDBTL_TOKEN=cbtl_...             # cloudbtl token create -n jevrag
export TYPESAFE_API_KEY=...                # optional

jevrag options prop_...                    # what Jev would see
jevrag ask "which quotes include supervisor day rates" --source onedrive
```

Land documents first with the CloudBTL CLI (`cloudbtl land <files...> -s onedrive`) and let an
enricher write `fields.*` / `faq.*` descriptors; JevRAG reads only structured descriptors and never
sends document bodies to the decision model.
