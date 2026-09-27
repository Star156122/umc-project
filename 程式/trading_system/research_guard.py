"""開發期日期防線；沒有命令列、環境變數或設定檔解鎖捷徑。"""
from datetime import date, datetime
from zoneinfo import ZoneInfo
from pathlib import Path
import json

POLICY = Path(__file__).resolve().parents[1] / 'configs/holdout_policy.json'
MESSAGE = ('Holdout period is locked. This period is reserved for final out-of-sample '
           'evaluation and cannot be used during strategy development. '
           '2025/07/01～2025/12/31 為鎖定保留資料，請勿用於開發、比較或報告。')


class HoldoutLockedError(ValueError):
    pass


def _date(value):
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def assert_development_period(start, end):
    policy = json.loads(POLICY.read_text(encoding='utf-8'))
    # 防止只改設定檔就意外移除日期鎖；正式驗證須另外審核執行流程。
    if policy.get('holdout') != {'start': '2025-07-01', 'end': '2025-12-31', 'access': 'forbidden'}:
        raise HoldoutLockedError('Holdout policy changed unexpectedly; development execution blocked.')
    first, last = _date(start), _date(end)
    if first > last:
        raise ValueError('開始日期不能晚於結束日期')
    if first <= date(2025, 12, 31) and last >= date(2025, 7, 1):
        raise HoldoutLockedError(MESSAGE)


def assert_config(config):
    assert_development_period(config.backtest_start, config.backtest_end)
    if hasattr(config, 'backfill_start'):
        assert_development_period(config.backfill_start, config.backfill_end)


def assert_timestamps(stamps):
    stamps=list(stamps)
    if stamps:
        first=datetime.fromtimestamp(float(min(stamps)), ZoneInfo('Asia/Taipei')).date()
        last=datetime.fromtimestamp(float(max(stamps)), ZoneInfo('Asia/Taipei')).date()
        assert_development_period(first, last)


def assert_payload(value):
    """檢查報告／實驗的日期中繼資料，不把保留期宣告當成實際存取。"""
    if isinstance(value, dict):
        for a, b in (('backtest_start','backtest_end'), ('period_start','period_end'),
                     ('range_start','range_end'), ('start_date','end_date'), ('start','end')):
            if a in value and b in value:
                assert_development_period(value[a], value[b])
        for key, child in value.items():
            if key in ('holdout', 'independent_validation_period', 'reserved_period'):
                continue
            if key in ('period', 'development_period') and isinstance(child, list) and len(child) == 2:
                assert_development_period(*child)
            elif isinstance(child, (dict, list)):
                assert_payload(child)
    elif isinstance(value, list):
        for child in value:
            assert_payload(child)
    return value


def guarded_json_loads(text, *args, **kwargs):
    return assert_payload(json.loads(text, *args, **kwargs))
