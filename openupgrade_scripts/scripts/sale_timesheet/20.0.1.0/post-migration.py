# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def fill_reinvoice_move_id(env):
    """account.analytic.line#timesheet_invoice_id (sale_timesheet) is replaced
    by #reinvoice_move_id (sale): same meaning, the invoice the line was billed
    in, used by _is_not_billed()."""
    if not openupgrade.column_exists(
        env.cr, "account_analytic_line", "timesheet_invoice_id"
    ):
        return
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE account_analytic_line aal
        SET reinvoice_move_id = aal.timesheet_invoice_id
        FROM account_move am
        WHERE am.id = aal.timesheet_invoice_id
            AND aal.reinvoice_move_id IS NULL
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    # the ir.access part of the noupdate changes (deactivation of
    # account.account_analytic_line_rule_billing_user/readonly_user) is done by
    # base; here only the timesheet report attached to the invoice templates
    openupgrade.load_data(env, "sale_timesheet", "20.0.1.0/noupdate_changes.xml")
    fill_reinvoice_move_id(env)
