"""Shared POST body size limits for stock-desk and instruction-desk /inventory/ proxy.

JSON bodies that embed Base64 files need headroom beyond the raw CSV size
(~4/3 Base64 expansion plus JSON wrapper keys). Keep a hard cap — never unlimited.
"""

# Default JSON POST body limit (bytes, Content-Length).
DEFAULT_BODY_MAX_BYTES = 1_000_000

# Raised limit for Base64 CSV / workbook uploads (same class as legacy /api/import).
# Chosen to match the existing legacy import allowlist (12_000_000).
# Raw decoded CSV remains capped at 8_000_000 in legacy.decode_csv_bytes
# (≈ 8MB * 4/3 ≈ 10.7MB Base64 + JSON wrapper fits under 12MB).
LARGE_BODY_MAX_BYTES = 12_000_000

# Paths that may send Base64 file payloads in the JSON body.
LARGE_BODY_PATHS = frozenset({
    '/api/import',
    '/api/legacy-csv/preview',
    '/api/legacy-csv/commit',
    '/api/legacy-csv/history',
    '/api/product-ledger-csv/preview',
    '/api/product-ledger-csv/commit',
    '/api/product-ledger-csv/history',
    '/api/product-ledger-csv/maker-lookup',
    '/api/product-ledger-csv/maker-lookup/adopt',
})

# Shown to the UI when Content-Length exceeds the path's limit.
OVERSIZE_BODY_ERROR = 'ファイル容量が上限を超えています'


def body_limit_for(path: str) -> int:
    return LARGE_BODY_MAX_BYTES if path in LARGE_BODY_PATHS else DEFAULT_BODY_MAX_BYTES
