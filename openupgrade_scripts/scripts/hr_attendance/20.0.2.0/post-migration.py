# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade

_removed_models = [
    "hr.attendance.overtime.line",
    "hr.attendance.overtime.rule",
    "hr.attendance.overtime.ruleset",
]


def _map_attendance_validation(cr):
    """res.company attendance_overtime_validation ('no_validation',
    'by_manager') -> attendance_validation ('no_validation',
    'manual_validation', 'tolerance_validation'). In 20.0 the validation
    applies to the attendance itself (the overtime is an attendance computed
    by the time rules)."""
    if not openupgrade.column_exists(
        cr, "res_company", "attendance_overtime_validation"
    ):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_company SET attendance_validation = 'manual_validation'
        WHERE attendance_overtime_validation = 'by_manager'
        """,
    )


def _log_not_converted_overtime_rules(cr):
    """The overtime rules (hr.attendance.overtime.ruleset / .rule) are replaced
    by hr.time.rule (hr_work_entry), which comes with its own data (a generic
    "Employee Schedule Rule" and the rules of several localizations), and the
    overtime lines by attendances computed from the time rules. The rules
    created or changed by users have no 1:1 counterpart (rates live on the
    work entry type now): they are kept in the legacy tables and reported."""
    if not openupgrade.table_exists(cr, "hr_attendance_overtime_rule"):
        return
    cr.execute(
        """
        SELECT r.id, r.name FROM hr_attendance_overtime_rule r
        LEFT JOIN ir_model_data imd
            ON imd.model = 'hr.attendance.overtime.rule' AND imd.res_id = r.id
        WHERE imd.id IS NULL OR r.write_date > r.create_date
        """
    )
    for rule_id, name in cr.fetchall():
        openupgrade.logger.warning(
            "Overtime rule %s (%s) is not converted to a time rule (hr.time.rule)",
            rule_id,
            name,
        )
    if openupgrade.column_exists(cr, "res_company", "overtime_company_threshold"):
        cr.execute(
            """
            SELECT id FROM res_company
            WHERE COALESCE(overtime_company_threshold, 0) <> 0
                OR COALESCE(overtime_employee_threshold, 0) <> 0
            """
        )
        for (company_id,) in cr.fetchall():
            openupgrade.logger.warning(
                "Overtime thresholds of company %s are not converted: set the "
                "tolerances on the time rules",
                company_id,
            )


@openupgrade.migrate()
def migrate(env, version):
    _map_attendance_validation(env.cr)
    _log_not_converted_overtime_rules(env.cr)
    # Odoo never deletes the XML-IDs of records of removed models
    # (ir.model.data._process_end skips unknown models)
    openupgrade.delete_xmlids_of_removed_models(
        env.cr, _removed_models, modules=["hr_attendance"]
    )
