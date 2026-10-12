# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    """In v19 the analytic distribution of the MO project was always applied to
    the components and work orders. In v20 it requires
    stock.picking.type#analytic_costs, which v20 sets for the manufacturing
    operation types on install (data/project_mrp_account_data.xml, noupdate,
    not loaded on update) and in its compute. In v19 the field was invisible
    on manufacturing operation types (project_stock_account only shows it for
    receipts and deliveries), so a NULL or False there was never a user
    choice: set it on all of them to keep the v19 behaviour."""
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE stock_picking_type
        SET analytic_costs = TRUE
        WHERE code = 'mrp_operation' AND analytic_costs IS NOT TRUE
        """,
    )
