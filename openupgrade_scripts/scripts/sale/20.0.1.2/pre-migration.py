# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade

_renamed_fields = [
    # Same field, new name: selection keys ('no', 'cost', 'sales_price') and
    # semantics are unchanged.
    ("product.template", "product_template", "expense_policy", "reinvoice_policy"),
    ("product.product", "product_product", "expense_policy", "reinvoice_policy"),
]


def _column_type(cr, table, column):
    cr.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = 'public' AND table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    row = cr.fetchone()
    return row[0] if row else None


def _sale_delay_to_legacy(env):
    """product.template.sale_delay moves from stock to sale and becomes
    company_dependent (jsonb). Odoo would run ALTER ... TYPE jsonb USING
    sale_delay::jsonb, which fails for integer columns ("cannot cast type
    integer to jsonb") and aborts the update. Move the old values away; the
    ORM creates a fresh jsonb column and post-migration converts the values
    that differ from the default."""
    if _column_type(env.cr, "product_template", "sale_delay") in (
        "integer",
        "double precision",
        "numeric",
    ):
        openupgrade.rename_columns(env.cr, {"product_template": [("sale_delay", None)]})


def _precreate_document_tax_mode(env):
    """New required stored computed field. Filling it here with the value its
    compute would give (company setting) avoids that the ORM computes it for
    every order, which would mark the amounts of every order line for
    recomputation (_compute_amount depends on document_tax_mode). In v19 a tax
    without price_include_override followed the company setting, which is
    exactly what the document mode reproduces, so stored amounts stay valid."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "sale_order", "document_tax_mode"):
        openupgrade.logged_query(
            cr, "ALTER TABLE sale_order ADD COLUMN document_tax_mode VARCHAR"
        )
    if openupgrade.column_exists(cr, "res_company", "account_price_include"):
        openupgrade.logged_query(
            cr,
            """
            UPDATE sale_order so
            SET document_tax_mode = COALESCE(rc.account_price_include, 'tax_excluded')
            FROM res_company rc
            WHERE rc.id = so.company_id
                AND so.document_tax_mode IS NULL
            """,
        )
    openupgrade.logged_query(
        cr,
        """
        UPDATE sale_order
        SET document_tax_mode = 'tax_excluded'
        WHERE document_tax_mode IS NULL
        """,
    )


def _precreate_section_fields(env):
    """New stored computed fields on sale.order.line, only meaningful for
    (sub)section lines. Pre-fill them like their computes do, so that the ORM
    does not have to compute them for every line, and so that the new
    constraint section_fields_null holds."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "sale_order_line", "section_qty"):
        openupgrade.logged_query(
            cr, "ALTER TABLE sale_order_line ADD COLUMN section_qty NUMERIC"
        )
        openupgrade.logged_query(
            cr,
            """
            UPDATE sale_order_line
            SET section_qty = CASE
                WHEN display_type IN ('line_section', 'line_subsection') THEN 1.0
                ELSE 0.0 END
            """,
        )
    if not openupgrade.column_exists(cr, "sale_order_line", "section_uom_id"):
        openupgrade.logged_query(
            cr, "ALTER TABLE sale_order_line ADD COLUMN section_uom_id INTEGER"
        )
        openupgrade.logged_query(
            cr,
            """
            UPDATE sale_order_line
            SET section_uom_id = (
                SELECT res_id FROM ir_model_data
                WHERE module = 'uom' AND name = 'product_uom_unit'
            )
            WHERE display_type IN ('line_section', 'line_subsection')
            """,
        )


def _require_payment_to_prepayment_percent(env):
    """v20 drops sale.order.require_payment: an order requires an online payment
    when prepayment_percent > 0. In v19 prepayment_percent was filled from the
    company (1.0 by default) even when no payment was required, so without this
    every such quotation would ask for a full prepayment in v20."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "sale_order", "require_payment"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE sale_order
        SET prepayment_percent = 0.0
        WHERE require_payment IS NOT TRUE
            AND prepayment_percent IS DISTINCT FROM 0.0
        """,
    )


def _delete_act_window_becoming_server_action(env):
    """sale.action_accrued_revenue_entry_sale_order_line is an
    ir.actions.act_window in v19 and an ir.actions.server in v20. Loading the
    new record under the same XML-ID raises "found record of different model",
    so remove the old window action (its binding included) and its XML-ID."""
    cr = env.cr
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM ir_act_window
        WHERE id IN (
            SELECT res_id FROM ir_model_data
            WHERE module = 'sale'
                AND name = 'action_accrued_revenue_entry_sale_order_line'
                AND model = 'ir.actions.act_window'
        )
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM ir_model_data
        WHERE module = 'sale'
            AND name = 'action_accrued_revenue_entry_sale_order_line'
            AND model = 'ir.actions.act_window'
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.rename_fields(env, _renamed_fields)
    _sale_delay_to_legacy(env)
    _precreate_document_tax_mode(env)
    _precreate_section_fields(env)
    _require_payment_to_prepayment_percent(env)
    _delete_act_window_becoming_server_action(env)
