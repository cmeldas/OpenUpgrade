# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


def _company_default_template_to_ir_default(env):
    """res.company.sale_order_template_id (the 'Default Template' setting) is
    gone in v20. sale.order.sale_order_template_id is a plain stored field now,
    so keep the behaviour with a company specific ir.default."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "res_company", "sale_order_template_id"):
        return
    openupgrade.logged_query(
        cr,
        """
        INSERT INTO ir_default (
            field_id, company_id, json_value,
            create_uid, create_date, write_uid, write_date
        )
        SELECT f.id, rc.id, rc.sale_order_template_id::text,
            1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC'
        FROM res_company rc
        JOIN ir_model_fields f
            ON f.model = 'sale.order' AND f.name = 'sale_order_template_id'
        JOIN sale_order_template sot ON sot.id = rc.sale_order_template_id
        WHERE NOT EXISTS (
            SELECT 1 FROM ir_default d
            WHERE d.field_id = f.id
                AND d.company_id = rc.id
                AND d.user_id IS NULL
                AND d.condition IS NULL
        )
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _company_default_template_to_ir_default(env)
