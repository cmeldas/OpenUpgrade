# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

# v19 sale_timesheet timesheet_invoice_type -> v20 billable_type, only used to
# report differences in the log
_timesheet_invoice_type_mapping = {
    "billable_fixed": "02_billable_fixed",
    "billable_time": "04_billable_time",
    "billable_milestones": "06_billable_milestones",
    "billable_manual": "08_billable_manual",
    "non_billable": "09_non_billable",
    "service_revenues": "10_service_revenues",
    "other_revenues": "11_other_revenues",
    "other_costs": "30_other_costs",
}


def recompute_billable_type(env):
    """account.analytic.line#billable_type and #category_report are new stored
    computed fields of sale_project. Their columns were computed when
    sale_project was loaded, without the overrides of the modules loaded after
    it (sale_timesheet, sale_project_stock, sale_purchase_project...), which
    is also why sale_timesheet's post_init_hook recomputes them. Recompute
    them now that the whole registry is loaded."""
    lines = env["account.analytic.line"].with_context(active_test=False).search([])
    for batch in openupgrade.chunked(lines, single=False):
        env.add_to_compute(batch._fields["billable_type"], batch)
        env.add_to_compute(batch._fields["category_report"], batch)
        batch._recompute_recordset(["billable_type", "category_report"])
    env.flush_all()
    legacy = "timesheet_invoice_type"
    if not openupgrade.column_exists(env.cr, "account_analytic_line", legacy):
        return
    env.cr.execute(
        f"""
        SELECT {legacy}, billable_type, count(*)
        FROM account_analytic_line
        WHERE {legacy} IS NOT NULL
        GROUP BY 1, 2
        """
    )
    for old, new, count in env.cr.fetchall():
        expected = _timesheet_invoice_type_mapping.get(old)
        log = _logger.info if new == expected else _logger.warning
        log(
            "account.analytic.line: %s line(s) with v19 timesheet_invoice_type "
            "%s now have billable_type %s",
            count,
            old,
            new,
        )


@openupgrade.migrate()
def migrate(env, version):
    recompute_billable_type(env)
