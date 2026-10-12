# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


def _fill_sale_delay(env):
    """Convert the legacy integer sale_delay (v19, module stock) into the v20
    company_dependent jsonb column. Default values (0) are left NULL, as Odoo
    does for company dependent fields; other values are stored for every
    company the product is available in."""
    cr = env.cr
    legacy = openupgrade.get_legacy_name("sale_delay")
    if not openupgrade.column_exists(cr, "product_template", legacy):
        return
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE product_template pt
        SET sale_delay = sub.value
        FROM (
            SELECT pt2.id,
                jsonb_object_agg(rc.id::text, to_jsonb(pt2.{legacy})) AS value
            FROM product_template pt2
            JOIN res_company rc
                ON pt2.company_id IS NULL OR rc.id = pt2.company_id
            WHERE COALESCE(pt2.{legacy}, 0) <> 0
            GROUP BY pt2.id
        ) AS sub
        WHERE sub.id = pt.id
        """,
    )


def _invoice_policy_default_to_company(env):
    """v19 stored the 'Invoicing Policy' setting as an ir.default of
    product.template.invoice_policy; v20 stores it on the company
    (res.company.sale_invoice_policy) and uses it in the compute of the
    product field. Move the value over and drop the stale ir.default, which
    would otherwise override the company setting on every new product."""
    cr = env.cr
    default_query = """
        FROM ir_default d
        JOIN ir_model_fields f ON f.id = d.field_id
        WHERE f.model = 'product.template'
            AND f.name = 'invoice_policy'
            AND d.user_id IS NULL
            AND d.condition IS NULL
            AND d.json_value IN ('"order"', '"delivery"')
    """
    # company specific defaults first, then the global one for the others
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE res_company rc
        SET sale_invoice_policy = d.json_value::jsonb #>> '{{}}'
        {default_query}
            AND d.company_id = rc.id
        """,
    )
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE res_company rc
        SET sale_invoice_policy = d.json_value::jsonb #>> '{{}}'
        {default_query}
            AND d.company_id IS NULL
            AND NOT EXISTS (
                SELECT 1 FROM ir_default d2
                WHERE d2.field_id = d.field_id
                    AND d2.company_id = rc.id
                    AND d2.user_id IS NULL
                    AND d2.condition IS NULL
                    AND d2.json_value IN ('"order"', '"delivery"')
            )
        """,
    )
    openupgrade.logged_query(
        cr,
        f"""
        DELETE FROM ir_default
        WHERE id IN (SELECT d.id {default_query})
        """,
    )


def _automatic_invoice_to_company(env):
    """The global system parameter sale.automatic_invoice (v19) became the
    company field sale_automatic_invoice (v20)."""
    cr = env.cr
    cr.execute(
        "SELECT value FROM ir_config_parameter WHERE key = 'sale.automatic_invoice'"
    )
    row = cr.fetchone()
    if not row or (row[0] or "").strip().lower() in ("", "false", "0", "none"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE res_company
        SET sale_automatic_invoice = TRUE
        WHERE sale_automatic_invoice IS NOT TRUE
        """,
    )
    # keep the cron in line with the setting, as res.company.write() does
    env["res.company"]._sync_automatic_invoice_cron()


def _fill_reinvoice_policy(env):
    """reinvoice_policy keeps the column of expense_policy (renamed in
    pre-migration), so the ORM does not apply its default 'no' to rows where it
    is empty. Products created during the migration by modules loaded before
    sale (e.g. point_of_sale's 'Service Fee' product) are inserted while the
    field is not in the registry yet and end up with NULL, unlike in a fresh
    database."""
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE product_template
        SET reinvoice_policy = 'no'
        WHERE reinvoice_policy IS NULL
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _fill_reinvoice_policy(env)
    _fill_sale_delay(env)
    _invoice_policy_default_to_company(env)
    _automatic_invoice_to_company(env)
    # Only email_layout_xmlid of the noupdate mail templates: the layout moved
    # from the code (v19 action_quotation_send / _send_order_notification_mail)
    # to the templates. body_html/subject/name are left untouched on purpose:
    # the v20 texts have no translations in most languages yet, and replacing
    # them would drop working translations of the v19 texts.
    openupgrade.load_data(env, "sale", "20.0.1.2/noupdate_changes.xml")
