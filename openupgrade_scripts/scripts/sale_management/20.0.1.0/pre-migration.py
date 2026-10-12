# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # sale.order.template.require_payment is dropped in v20: a template asks
    # for an online payment when prepayment_percent > 0 (same change as on
    # sale.order, see sale pre-migration). In v19 prepayment_percent was
    # filled from the company even without require_payment.
    if openupgrade.column_exists(env.cr, "sale_order_template", "require_payment"):
        openupgrade.logged_query(
            env.cr,
            """
            UPDATE sale_order_template
            SET prepayment_percent = 0.0
            WHERE require_payment IS NOT TRUE
                AND prepayment_percent IS DISTINCT FROM 0.0
            """,
        )
