"""Shared POST body size limits for stock-desk and instruction-desk /inventory/ proxy.

JSON bodies that embed Base64 files need headroom beyond the raw file size
(~4/3 Base64 expansion plus JSON wrapper keys). Keep a hard cap — never unlimited.

The 12MB POST cap fits one CSV or workbook (raw decode stays 8MB).
Product registration and opening-stock registration send that CSV together with
two legacy JSON files. The observed old-stock export is 8,163,189 bytes
(14,775 rows), which is already over the old 8MB raw cap, and with the product
ledger CSV (1,852,305) plus old-customer JSON (83,153) the Base64 body is
13,464,864 bytes, over 12MB. Those two paths use a larger finite bundle cap.
"""

# Default JSON POST body limit (bytes, Content-Length).
DEFAULT_BODY_MAX_BYTES = 1_000_000

# One Base64 CSV / workbook (same class as legacy /api/import).
# Raw decoded CSV remains capped at RAW_CSV_MAX_BYTES in legacy.decode_csv_bytes
# (≈ 8MB * 4/3 ≈ 10.7MB Base64 + JSON wrapper fits under 12MB).
RAW_CSV_MAX_BYTES = 8_000_000
LARGE_BODY_MAX_BYTES = 12_000_000

# One legacy JSON file (old stock or old customer), after Base64 decode.
# 12MB covers the 8,163,189-byte stock export and modest growth of the same file.
RAW_LEGACY_JSON_MAX_BYTES = 12_000_000

# Product-ledger preview/commit may carry the ledger CSV plus both JSON files.
# Cap is the Base64 size of one max CSV and two max JSON files, plus 1MB of
# wrapper keys. Still finite: one extra raw byte is rejected before this.
def _b64_len(n: int) -> int:
    return 4 * ((n + 2) // 3)


LEDGER_BUNDLE_WRAPPER_BYTES = 1_000_000
LEDGER_BUNDLE_MAX_BYTES = (
    _b64_len(RAW_CSV_MAX_BYTES)
    + _b64_len(RAW_LEGACY_JSON_MAX_BYTES)
    + _b64_len(RAW_LEGACY_JSON_MAX_BYTES)
    + LEDGER_BUNDLE_WRAPPER_BYTES
)

# Paths that may send Base64 file payloads in the JSON body.
LARGE_BODY_PATHS = frozenset({
    '/api/import',
    '/api/prices/initial',
    '/api/legacy-csv/preview',
    '/api/legacy-csv/commit',
    '/api/legacy-csv/history',
    '/api/qr-link/preview',
    '/api/qr-link/commit',
    '/api/product-ledger-csv/preview',
    '/api/product-ledger-csv/commit',
    '/api/product-ledger-csv/history',
    '/api/product-ledger-csv/maker-lookup',
    '/api/product-ledger-csv/maker-lookup/from-url',
    '/api/product-ledger-csv/maker-lookup/adopt',
    '/api/product-ledger-csv/maker-lookup/bulk/plan',
    '/api/product-ledger-csv/maker-lookup/bulk/start',
    '/api/product-ledger-csv/maker-lookup/bulk/status',
    '/api/product-ledger-csv/maker-lookup/bulk/cancel',
})

# Preview and commit carry the ledger CSV and the legacy JSON files together.
LEDGER_BUNDLE_PATHS = frozenset({
    '/api/product-ledger-csv/preview',
    '/api/product-ledger-csv/commit',
})

# Shown to the UI when Content-Length exceeds the path's limit.
OVERSIZE_BODY_ERROR = 'ファイル容量が上限を超えています'


def body_limit_for(path: str) -> int:
    if path in LEDGER_BUNDLE_PATHS:
        return LEDGER_BUNDLE_MAX_BYTES
    return LARGE_BODY_MAX_BYTES if path in LARGE_BODY_PATHS else DEFAULT_BODY_MAX_BYTES
