# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


def _fill_attendance_state(env):
    """hr.attendance.state is new and required, its default 'draft' would put
    every past attendance back to validation. 19.0 only validated the
    overtime (overtime_status): attendances are validated, except those whose
    overtime still waited for the approval of a manager."""
    cr = env.cr
    openupgrade.add_columns(
        env, [("hr.attendance", "state", "selection", None, "hr_attendance")]
    )
    to_approve = (
        "WHEN overtime_status = 'to_approve' THEN 'draft'"
        if openupgrade.column_exists(cr, "hr_attendance", "overtime_status")
        else ""
    )
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE hr_attendance
        SET state = CASE {to_approve} ELSE 'validated' END
        WHERE state IS NULL
        """,
    )


def _fill_attendance_work_entry_type(env):
    """hr.attendance.work_entry_type_id is new and required, its default is
    res.company.attendance_work_entry_type_id (new as well). Apply the logic
    of ResCompany._compute_attendance_work_entry_type_id: the working time
    type with code 002.00 of the country of the company, else the generic
    attendance type of hr_work_entry (installed as new dependency before)."""
    cr = env.cr
    cr.execute("SELECT 1 FROM hr_attendance LIMIT 1")
    if not cr.fetchone():
        return
    openupgrade.add_columns(
        env,
        [("hr.attendance", "work_entry_type_id", "many2one", None, "hr_attendance")],
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE hr_attendance att
        SET work_entry_type_id = COALESCE(
            (SELECT wet.id FROM hr_work_entry_type wet
             WHERE wet.country_id = rp.country_id
                AND wet.count_as = 'working_time' AND wet.code = '002.00'
             ORDER BY wet.id LIMIT 1),
            (SELECT imd.res_id FROM ir_model_data imd
             WHERE imd.module = 'hr_work_entry'
                AND imd.name = 'generic_work_entry_type_attendance'))
        FROM hr_employee emp
        JOIN res_company rc ON rc.id = emp.company_id
        JOIN res_partner rp ON rp.id = rc.partner_id
        WHERE emp.id = att.employee_id AND att.work_entry_type_id IS NULL
        """,
    )


def _fix_attendance_date(cr):
    """OpenUpgrade 19.0 (hr_attendance/19.0.2.0 pre-migration) prefilled the
    stored compute hr.attendance.date with
    (check_in AT TIME ZONE zone)::date, which reads the UTC check_in as a local
    time (shift in the wrong direction). Odoo computes
    check_in.replace(tzinfo=UTC).astimezone(tz).date(). Recompute the rows
    that still hold the wrong value (same zone as the 19.0 script: working
    schedule, resource, company schedule; resource_calendar.tz is a legacy
    column now) with the timezone of the employee in 20.0 (current version)."""
    if not openupgrade.column_exists(cr, "resource_calendar", "tz"):
        return
    openupgrade.logged_query(
        cr,
        """
        WITH zones AS (
            SELECT e.id AS employee_id,
                COALESCE(cal.tz, rr.tz, ccal.tz, 'UTC') AS old_zone,
                COALESCE(v.tz, rr.tz, rc.tz, 'UTC') AS new_zone
            FROM hr_employee e
            JOIN resource_resource rr ON rr.id = e.resource_id
            LEFT JOIN resource_calendar cal ON cal.id = rr.calendar_id
            LEFT JOIN res_company rc ON rc.id = e.company_id
            LEFT JOIN resource_calendar ccal ON ccal.id = rc.resource_calendar_id
            LEFT JOIN hr_version v ON v.id = e.current_version_id
        )
        UPDATE hr_attendance att
        SET date = (att.check_in AT TIME ZONE 'UTC' AT TIME ZONE z.new_zone)::date
        FROM zones z
        WHERE z.employee_id = att.employee_id
            AND att.check_in IS NOT NULL
            AND att.date = (att.check_in AT TIME ZONE z.old_zone)::date
            AND att.date <> (
                att.check_in AT TIME ZONE 'UTC' AT TIME ZONE z.new_zone)::date
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _fix_attendance_date(env.cr)
    _fill_attendance_state(env)
    _fill_attendance_work_entry_type(env)
