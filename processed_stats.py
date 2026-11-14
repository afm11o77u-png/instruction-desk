r"""
処理済み指示書枚数 集計モジュール (processed_stats.py)
\\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\処理済み 配下の
月別・日別フォルダ内の指示書ファイル数を集計する。
"""

import os
import re
import json
import time
from datetime import datetime, date
from pathlib import Path

DEFAULT_PROCESSED_DIR = r"\\Desktop-iu7d38q\e\札幌発寒支店共有\【指示書フォルダ】\処理済み"
CACHE_FILE = Path(__file__).resolve().parent / "data" / "processed_stats_cache.json"

WEEKDAYS_JA = ["月", "火", "水", "木", "金", "土", "日"]

def get_processed_dir() -> Path:
    env_path = os.environ.get("PROCESSED_FOLDER_BASE")
    if env_path:
        return Path(env_path)
    return Path(DEFAULT_PROCESSED_DIR)

def _parse_day_info(name: str, default_year: int, default_month: int) -> tuple[str, str, str, int]:
    """日フォルダ名から (YYYY-MM-DD, 表示ラベル 'M月D日', 曜日, 日番号) を返す"""
    # 20260801
    m1 = re.match(r"^(\d{4})(\d{2})(\d{2})$", name)
    if m1:
        y, m, d = int(m1.group(1)), int(m1.group(2)), int(m1.group(3))
        try:
            dt = date(y, m, d)
            w = WEEKDAYS_JA[dt.weekday()]
        except ValueError:
            w = ""
        return f"{y:04d}-{m:02d}-{d:02d}", f"{m}月{d}日", w, d

    # 2024.5.20 or 2026.01.05 or 2024.5.17.1
    m2 = re.match(r"^(\d{4})[./\-](\d{1,2})[./\-](\d{1,2})(?:\.\d+)?$", name)
    if m2:
        y, m, d = int(m2.group(1)), int(m2.group(2)), int(m2.group(3))
        try:
            dt = date(y, m, d)
            w = WEEKDAYS_JA[dt.weekday()]
        except ValueError:
            w = ""
        return f"{y:04d}-{m:02d}-{d:02d}", f"{m}月{d}日", w, d

    # 15日 または 15
    m3 = re.match(r"^(\d{1,2})日?$", name)
    if m3:
        d = int(m3.group(1))
        try:
            dt = date(default_year, default_month, d)
            w = WEEKDAYS_JA[dt.weekday()]
        except ValueError:
            w = ""
        return f"{default_year:04d}-{default_month:02d}-{d:02d}", f"{default_month}月{d}日", w, d

    return f"{default_year:04d}-{default_month:02d}-99", name, "", 99

def _scan_month_folder(month_path: str, year: int, month: int) -> dict:
    """指定の月フォルダ内の全日フォルダとファイル数を集計"""
    days = []
    month_total = 0

    if not os.path.exists(month_path):
        return {"year": year, "month": month, "total": 0, "days": []}

    try:
        entries = sorted(os.listdir(month_path))
    except Exception:
        entries = []

    for d_name in entries:
        d_path = os.path.join(month_path, d_name)
        if not os.path.isdir(d_path):
            continue

        try:
            # 隠しファイルやExcel一時ファイル (~$) を除外
            files = [f for f in os.listdir(d_path) if not f.startswith("~$") and os.path.isfile(os.path.join(d_path, f))]
            cnt = len(files)
        except Exception:
            cnt = 0

        date_str, day_label, weekday, day_num = _parse_day_info(d_name, year, month)
        days.append({
            "folder": d_name,
            "date": date_str,
            "label": day_label,
            "weekday": weekday,
            "day": day_num,
            "count": cnt
        })
        month_total += cnt

    days.sort(key=lambda x: (x["date"], x["day"], x["folder"]))

    return {
        "year": year,
        "month": month,
        "key": f"{year:04d}-{month:02d}",
        "label": f"{year}年{month}月",
        "total": month_total,
        "day_count": len(days),
        "days": days
    }

def get_stats(force_refresh: bool = False) -> dict:
    """処理済み指示書枚数の全集計データを返す"""
    now = datetime.now()
    current_ym = f"{now.year:04d}-{now.month:02d}"

    cache_data = None
    if not force_refresh and CACHE_FILE.exists():
        try:
            cache_data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            cache_data = None

    base = get_processed_dir()
    base_exists = False
    try:
        base_exists = base.exists()
    except Exception:
        base_exists = False

    if not base_exists:
        if cache_data and isinstance(cache_data.get("months"), list):
            if "weekdayMedians" not in cache_data:
                try:
                    import statistics
                    by_w = {"月": [], "火": [], "水": [], "木": [], "金": [], "土": [], "日": []}
                    for md in cache_data["months"]:
                        for day in md.get("days", []):
                            w = day.get("weekday")
                            cnt = day.get("count")
                            if w in by_w and cnt is not None:
                                by_w[w].append(cnt)
                    wm = {}
                    for w in WEEKDAYS_JA:
                        vals = by_w.get(w, [])
                        if vals:
                            wm[w] = round(statistics.median(vals))
                    cache_data["weekdayMedians"] = wm
                except Exception:
                    cache_data["weekdayMedians"] = {}
            return cache_data
        return {
            "exists": False,
            "dir": str(base),
            "years": [],
            "total_all": 0,
            "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "months": [],
            "weekdayMedians": {}
        }

    # 月フォルダの列挙
    month_dirs = []
    try:
        base_items = sorted(os.listdir(str(base)))
    except Exception:
        base_items = []

    for item in base_items:
        ipath = os.path.join(str(base), item)
        if not os.path.isdir(ipath):
            continue

        m = re.match(r"^(\d{4})\.(\d{1,2})月?$", item)
        if m:
            month_dirs.append((int(m.group(1)), int(m.group(2)), ipath, item))
        elif re.match(r"^\d{4}$", item):
            try:
                sub_items = sorted(os.listdir(ipath))
            except Exception:
                sub_items = []
            for sub in sub_items:
                subpath = os.path.join(ipath, sub)
                if os.path.isdir(subpath):
                    sm = re.match(r"^(\d{4})\.(\d{1,2})月?$", sub)
                    if sm:
                        month_dirs.append((int(sm.group(1)), int(sm.group(2)), subpath, sub))

    # 2024年以降を年・月順にソート
    month_dirs = [m for m in month_dirs if m[0] >= 2024]
    month_dirs.sort(key=lambda x: (x[0], x[1]))

    cached_months = {}
    if cache_data and isinstance(cache_data.get("months"), list):
        for cm in cache_data["months"]:
            cached_months[cm.get("key")] = cm

    months_result = []
    total_all = 0
    years_set = set()

    for y, m, path, name in month_dirs:
        key = f"{y:04d}-{m:02d}"
        years_set.add(y)

        # 過去月かつキャッシュがあればキャッシュを利用（今月のみ常に最新スキャン）
        if not force_refresh and key != current_ym and key in cached_months:
            m_data = cached_months[key]
        else:
            m_data = _scan_month_folder(path, y, m)

        months_result.append(m_data)
        total_all += m_data.get("total", 0)

    # 降順（最新月が上）で並べ替え
    months_result.sort(key=lambda x: (x["year"], x["month"]), reverse=True)
    sorted_years = sorted(list(years_set), reverse=True)

    # 全期間の曜日別中央値
    weekday_medians = {}
    try:
        import statistics
        by_weekday = {"月": [], "火": [], "水": [], "木": [], "金": [], "土": [], "日": []}
        for md in months_result:
            for day in md.get("days", []):
                w = day.get("weekday")
                cnt = day.get("count")
                if w in by_weekday and cnt is not None:
                    by_weekday[w].append(cnt)
        for w in WEEKDAYS_JA:
            vals = by_weekday.get(w, [])
            if vals:
                weekday_medians[w] = round(statistics.median(vals))
    except Exception:
        weekday_medians = {}

    result = {
        "exists": True,
        "dir": str(base),
        "years": sorted_years,
        "total_all": total_all,
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "months": months_result,
        "weekdayMedians": weekday_medians
    }

    # キャッシュ保存
    try:
        CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

    return result
