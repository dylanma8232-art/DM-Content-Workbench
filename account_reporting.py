from collections import defaultdict
from datetime import date, timedelta

from fastapi import HTTPException
from pydantic import BaseModel, Field


def initialize(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS reporting_periods (
        project_id INTEGER, platform TEXT, account TEXT, start TEXT, stop TEXT,
        PRIMARY KEY(project_id,platform,account,start))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS reporting_aliases (
        project_id INTEGER, platform TEXT, account TEXT, destination_platform TEXT, destination_account TEXT,
        PRIMARY KEY(project_id,platform,account))''')
    if 'registration' not in {row[1] for row in conn.execute('PRAGMA table_info(reporting_periods)')}:
        conn.execute('ALTER TABLE reporting_periods ADD COLUMN registration INTEGER NOT NULL DEFAULT 0')


def resolve(conn, project, platform, account):
    alias = conn.execute('SELECT destination_platform,destination_account FROM reporting_aliases WHERE (project_id,platform,account)=(?,?,?)',
                         (project, platform, account)).fetchone()
    return tuple(alias) if alias else (platform, account)


class PeriodChange(BaseModel):
    account: str = Field(min_length=1, max_length=100)
    platform: str = Field(min_length=1, max_length=40)
    day: date
    active: bool


def mount(app, database, project_settings, account_exists):
    @app.get('/api/projects/{project}/account-impact')
    def impact(project: int, platform: str, account: str):
        with database() as conn:
            project_settings(conn, project)
            count = conn.execute('SELECT COUNT(*) FROM account_daily WHERE (project_id,platform,account)=(?,?,?)', (project, platform, account)).fetchone()[0]
            return dict(project_id=project, platform=platform, account=account, reports=count)

    @app.put('/api/projects/{project}/reporting-period')
    def update(project: int, form: PeriodChange):
        key = (project, form.platform, form.account)
        day = form.day.isoformat()
        with database() as conn:
            conn.execute('BEGIN IMMEDIATE')
            project_settings(conn, project)
            if not account_exists(conn, key):
                raise HTTPException(404, '找不到此账号')
            latest = conn.execute('SELECT start,stop,registration FROM reporting_periods WHERE (project_id,platform,account)=(?,?,?) ORDER BY start DESC LIMIT 1', key).fetchone()
            if form.active:
                if latest and latest['start'] == day and latest['stop'] is None:
                    return {'saved': True}
                if latest and latest['registration'] and latest['stop'] is None and day < latest['start']:
                    conn.execute('UPDATE reporting_periods SET start=? WHERE (project_id,platform,account,start)=(?,?,?,?)',
                                 (day, *key, latest['start']))
                    return {'saved': True}
                if latest and (latest['stop'] is None or day <= latest['stop']):
                    raise HTTPException(409, '新的应报起日须在上一段停报日期之后')
                conn.execute('INSERT INTO reporting_periods(project_id,platform,account,start) VALUES(?,?,?,?)', (*key, day))
            elif latest and latest['stop'] == day:
                return {'saved': True}
            elif latest and latest['stop'] is None and day >= latest['start']:
                conn.execute('UPDATE reporting_periods SET stop=? WHERE (project_id,platform,account)=(?,?,?) AND stop IS NULL', (day, *key))
            else:
                raise HTTPException(409, '请先确认应报起日，停报日不能早于起日')
        return {'saved': True}

    @app.get('/api/projects/{project}/reporting-summary')
    def report(project: int, start: date, day: date):
        length = (day - start).days + 1
        if length < 1:
            raise HTTPException(422, '日期范围无效')
        limited = length > 366 or start.toordinal() <= length
        previous = start if limited else start - timedelta(days=length)
        with database() as conn:
            conn.execute('BEGIN')
            project_settings(conn, project)
            actual = [dict(row) for row in conn.execute('''SELECT platform, account, count(*) AS reports,
                max(day) AS latest FROM account_daily WHERE project_id=? AND day BETWEEN ? AND ?
                GROUP BY platform,account''', (project, start.isoformat(), day.isoformat()))]
            if limited:
                return dict(limited=True, actual=actual)
            accounts = conn.execute('SELECT platform,account FROM platform_accounts WHERE project_id=? UNION SELECT platform,account FROM account_daily WHERE project_id=? AND day<=?', (project, project, day.isoformat())).fetchall()
            periods = defaultdict(list)
            registrations = set()
            for row in conn.execute('SELECT * FROM reporting_periods WHERE project_id=?', (project,)):
                periods[row['platform'], row['account']].append((row['start'], row['stop']))
                if row['registration']:
                    registrations.add((row['platform'], row['account']))
            daily = defaultdict(dict)
            for row in conn.execute('SELECT * FROM account_daily WHERE project_id=? AND day BETWEEN ? AND ?', (project, previous.isoformat(), day.isoformat())):
                daily[row['platform'], row['account']][row['day']] = row
        days = [(previous + timedelta(days=index)).isoformat() for index in range(length * 2)]
        missing = []
        expected = reported = unknown = samples = 0
        diffs = dict(views=0, engagements=0, published=0)
        baseline = dict.fromkeys(diffs, 0)
        for platform, account in accounts:
            key = platform, account
            ranges = periods[key]
            records = daily[key]
            first_known = min((first for first, stop in ranges), default=None)
            if not ranges or (key not in registrations and start.isoformat() < first_known):
                unknown += 1
            eligible = [any(first <= stamp and (not stop or stamp < stop) for first, stop in ranges) for stamp in days]
            for stamp, due in zip(days[length:], eligible[length:]):
                if due:
                    expected += 1
                    if stamp in records:
                        reported += 1
                    else:
                        missing.append(dict(platform=platform, account=account, day=stamp))
            before = [stamp for stamp, due in zip(days[:length], eligible[:length]) if due]
            after = [stamp for stamp, due in zip(days[length:], eligible[length:]) if due]
            if (before and after and (key in registrations or days[0] >= first_known)
                    and all(stamp in records for stamp in before + after)):
                samples += 1
                for field in diffs:
                    prior = sum(records[stamp][field] for stamp in before)
                    baseline[field] += prior
                    diffs[field] += sum(records[stamp][field] for stamp in after) - prior
        return dict(expected=expected, reported=reported, unknown=unknown, missing=missing,
                    actual=actual, samples=samples, differences=diffs, baseline=baseline)
