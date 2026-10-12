# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from psycopg2 import sql

from openupgradelib import openupgrade

# Required fields removed in 20.0. Odoo drops the NOT NULL of a removed column
# only at the very end of the update (BaseModel._check_removed_columns), so any
# record created in between (data files, other migration scripts) would fail.
_removed_required_columns = [
    ("resource_calendar", "tz"),
    ("resource_calendar", "schedule_type"),
    ("resource_calendar_attendance", "name"),
]


def _drop_not_null_of_removed_columns(cr):
    for table, column in _removed_required_columns:
        if openupgrade.column_exists(cr, table, column):
            openupgrade.logged_query(
                cr,
                sql.SQL("ALTER TABLE {} ALTER COLUMN {} DROP NOT NULL").format(
                    sql.Identifier(table), sql.Identifier(column)
                ),
            )


def _delete_obsolete_attendance_lines(cr):
    """Lunch breaks and section lines are no attendance lines in 20.0.

    * 19.0 excluded day_period='lunch' lines from the working intervals. 20.0
      has no such value anymore: the break is the gap between the morning and
      the afternoon lines, a kept 12:00-13:00 line would become one hour of
      work (and its duration of 0 violates the new check_duration_hours).
    * display_type='line_section' lines only labelled the two weeks of a two
      weeks calendar, they have no hours.
    """
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM resource_calendar_attendance
        WHERE day_period = 'lunch' OR display_type IS NOT NULL
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM ir_model_data imd
        WHERE imd.model = 'resource.calendar.attendance'
            AND NOT EXISTS (
                SELECT 1 FROM resource_calendar_attendance rca
                WHERE rca.id = imd.res_id
            )
        """,
    )


def _convert_duration_based_calendars(cr):
    """resource.calendar.duration_based (19.0, per calendar) becomes
    resource.calendar.attendance.duration_based (20.0, stored compute: true
    when the line has no hours). Keep the duration of the lines of such
    calendars and drop their hours, so that 20.0 computes them as duration
    based lines."""
    if not openupgrade.column_exists(cr, "resource_calendar", "duration_based"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE resource_calendar_attendance rca
        SET duration_hours = COALESCE(
                NULLIF(rca.duration_hours, 0), rca.hour_to - rca.hour_from),
            hour_from = 0,
            hour_to = 0
        FROM resource_calendar rc
        WHERE rc.id = rca.calendar_id
            AND rc.duration_based
            AND (rca.hour_from <> 0 OR rca.hour_to <> 0)
        """,
    )


def _fill_calendar_type(env):
    """schedule_type ('flexible', 'fully_fixed') + two_weeks_calendar (19.0)
    -> calendar_type ('undefined', 'variable', 'fixed') (20.0).

    Filled before the ORM creates the column, so that the new stored computes
    depending on it (days_per_week, ...) see the right value."""
    cr = env.cr
    openupgrade.add_columns(
        env,
        [
            (
                "resource.calendar",
                "calendar_type",
                "selection",
                None,
                "resource_calendar",
            )
        ],
    )
    flexible = (
        "OR COALESCE(flexible_hours, FALSE)"
        if openupgrade.column_exists(cr, "resource_calendar", "flexible_hours")
        else ""
    )
    two_weeks = (
        "COALESCE(two_weeks_calendar, FALSE)"
        if openupgrade.column_exists(cr, "resource_calendar", "two_weeks_calendar")
        else "FALSE"
    )
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE resource_calendar
        SET calendar_type = CASE
            WHEN schedule_type = 'flexible' {flexible} THEN 'undefined'
            WHEN {two_weeks} THEN 'variable'
            ELSE 'fixed'
        END
        WHERE calendar_type IS NULL
        """,
    )


def _fill_company_tz(env):
    """res.company.tz is new (stored compute from the country). 19.0 took the
    timezone of the working schedules from resource.calendar.tz (removed):
    keep the one of the default working schedule of the company, then the
    one of the company partner. Companies left empty are computed in post."""
    cr = env.cr
    openupgrade.add_columns(
        env, [("res.company", "tz", "selection", None, "res_company")]
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_company rc
        SET tz = COALESCE(
            (SELECT cal.tz FROM resource_calendar cal
             WHERE cal.id = rc.resource_calendar_id),
            (SELECT rp.tz FROM res_partner rp WHERE rp.id = rc.partner_id)
        )
        WHERE rc.tz IS NULL
        """,
    )


def _add_reference_calendar(env):
    """reference_calendar_id is new with a default of the company working
    schedule, which the ORM would write on every existing calendar (the
    company calendar included, referencing itself). Existing calendars keep
    their full_time_required_hours from 19.0, so they get no reference."""
    openupgrade.add_columns(
        env,
        [
            (
                "resource.calendar",
                "reference_calendar_id",
                "many2one",
                None,
                "resource_calendar",
            )
        ],
    )


def _fill_leaves_count_as(env):
    """resource.calendar.leaves time_type ('leave', 'other') -> count_as
    ('absence', 'working_time')."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "resource_calendar_leaves", "time_type"):
        return
    openupgrade.add_columns(
        env,
        [
            (
                "resource.calendar.leaves",
                "count_as",
                "selection",
                None,
                "resource_calendar_leaves",
            )
        ],
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE resource_calendar_leaves
        SET count_as = CASE
            WHEN time_type = 'other' THEN 'working_time' ELSE 'absence' END
        WHERE count_as IS NULL
        """,
    )


def get_fully_flexible_calendar(cr, company_id):
    """Return a fully flexible calendar of the company, creating it if needed.

    In 19.0 a resource (or hr.version) without working schedule was fully
    flexible. In 20.0 the working schedule is required and full flexibility
    is a calendar of type 'undefined' without hours target."""
    cr.execute(
        """
        SELECT id FROM resource_calendar
        WHERE calendar_type = 'undefined'
            AND COALESCE(hours_per_week, 0) = 0
            AND COALESCE(hours_per_day, 0) = 0
            AND company_id IS NOT DISTINCT FROM %s
        ORDER BY active DESC, id
        LIMIT 1
        """,
        (company_id,),
    )
    row = cr.fetchone()
    if row:
        return row[0]
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO resource_calendar (
            name, company_id, active, calendar_type, hours_per_day,
            hours_per_week, full_time_required_hours,
            create_uid, create_date, write_uid, write_date)
        VALUES (
            'Fully Flexible', %s, TRUE, 'undefined', 0, 0, 0,
            1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC')
        RETURNING id
        """,
        (company_id,),
    )
    return cr.fetchone()[0]


def _fill_resource_calendar(cr):
    """resource.resource.calendar_id is now required."""
    cr.execute(
        "SELECT DISTINCT company_id FROM resource_resource WHERE calendar_id IS NULL"
    )
    for (company_id,) in cr.fetchall():
        calendar_id = get_fully_flexible_calendar(cr, company_id)
        openupgrade.logged_query(
            cr,
            """
            UPDATE resource_resource SET calendar_id = %s
            WHERE calendar_id IS NULL AND company_id IS NOT DISTINCT FROM %s
            """,
            (calendar_id, company_id),
        )


@openupgrade.migrate()
def migrate(env, version):
    cr = env.cr
    _drop_not_null_of_removed_columns(cr)
    _delete_obsolete_attendance_lines(cr)
    _convert_duration_based_calendars(cr)
    _fill_calendar_type(env)
    _fill_company_tz(env)
    _add_reference_calendar(env)
    _fill_leaves_count_as(env)
    _fill_resource_calendar(cr)
