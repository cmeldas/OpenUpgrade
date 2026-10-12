# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


def _convert_two_weeks_calendars(cr):
    """Two weeks calendars (19.0 week_type '0'/'1' on fixed lines) become
    variable calendars (pre-migration) whose lines recur every two weeks.

    19.0 resource.calendar.attendance.get_week_type():
    week_type = floor((date.toordinal() - 1) / 7) % 2, so the week starting on
    Monday 2024-01-08 has week_type 0 and the one starting on 2024-01-01 has
    week_type 1. Each line starts on its weekday of the matching week and
    repeats forever (recurrency_until = date.max, as computed by 20.0)."""
    if not openupgrade.column_exists(cr, "resource_calendar_attendance", "week_type"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE resource_calendar_attendance rca
        SET date = DATE '2024-01-08'
                + CASE WHEN rca.week_type = '1' THEN -7 ELSE 0 END
                + rca.dayofweek::integer,
            recurrency = TRUE,
            recurrency_type = 'weeks',
            recurrency_interval = 2,
            recurrency_end_type = 'forever',
            recurrency_until = DATE '9999-12-31'
        FROM resource_calendar rc
        WHERE rc.id = rca.calendar_id
            AND rc.calendar_type = 'variable'
            AND rca.week_type IN ('0', '1')
            AND rca.date IS NULL
        """,
    )


def _fill_days_per_week(cr):
    """days_per_week is a new stored compute, but 20.0 only computes it for
    fixed calendars (for the others the user sets it). Derive it from the
    hours targets kept from 19.0."""
    openupgrade.logged_query(
        cr,
        """
        UPDATE resource_calendar
        SET days_per_week = round((hours_per_week / hours_per_day)::numeric, 5)
        WHERE calendar_type <> 'fixed'
            AND COALESCE(days_per_week, 0) = 0
            AND COALESCE(hours_per_day, 0) > 0
            AND COALESCE(hours_per_week, 0) > 0
        """,
    )


def _recompute_day_period(env):
    """day_period is now a stored compute (morning / afternoon / full_day from
    the hours and calendar.hours_per_day). The column already exists, so the
    ORM does not compute it: values set by hand in 19.0 would stay as they are
    and be overwritten at the next change of the line only."""
    attendances = env["resource.calendar.attendance"].search([])
    env.add_to_compute(attendances._fields["day_period"], attendances)
    env.flush_all()


def _compute_company_tz(env):
    """res.company.tz could not be prefilled (no working schedule nor partner
    timezone): let 20.0 compute it from the country."""
    companies = env["res.company"].with_context(active_test=False).search(
        [("tz", "=", False)]
    )
    if companies:
        env.add_to_compute(companies._fields["tz"], companies)
        env.flush_all()


def _update_standard_calendar_name(cr):
    """noupdate change of resource.resource_calendar_std (name is not
    translatable): 'Standard 40 hours/week' -> '40 hours/week', when
    unchanged since 19.0."""
    openupgrade.logged_query(
        cr,
        """
        UPDATE resource_calendar rc SET name = '40 hours/week'
        FROM ir_model_data imd
        WHERE imd.module = 'resource' AND imd.name = 'resource_calendar_std'
            AND imd.model = 'resource.calendar' AND imd.res_id = rc.id
            AND rc.name = 'Standard 40 hours/week'
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _update_standard_calendar_name(env.cr)
    _convert_two_weeks_calendars(env.cr)
    _fill_days_per_week(env.cr)
    _recompute_day_period(env)
    _compute_company_tz(env)
