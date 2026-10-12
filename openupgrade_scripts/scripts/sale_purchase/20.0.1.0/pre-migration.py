# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).
from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # product.template.service_to_purchase ("Subcontract Service", a company
    # dependent boolean in v19) is replaced by the selection value
    # service_tracking = 'subcontract' (not company dependent). A service that
    # was subcontracted in any company keeps creating RfQs. The v20 compute
    # resets 'subcontract' when the product re-invoices costs, so do the same.
    # v20 write() raises a ValidationError on every write of a 'subcontract'
    # template without vendor (v19 only checked when the flag or the vendors
    # changed), so only map templates that have a vendor: without one, v19
    # could not create the RfQ either (UserError on order confirmation), and
    # mapping them would block any later ORM write on the product.
    cr = env.cr
    if not openupgrade.column_exists(cr, "product_template", "service_to_purchase"):
        return
    policy_column = (
        "reinvoice_policy"
        if openupgrade.column_exists(cr, "product_template", "reinvoice_policy")
        else "expense_policy"
    )
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE product_template
        SET service_tracking = 'subcontract'
        WHERE type = 'service'
            AND COALESCE(service_tracking, 'no') = 'no'
            AND COALESCE({policy_column}, 'no') = 'no'
            AND jsonb_typeof(service_to_purchase) = 'object'
            AND EXISTS (
                SELECT 1 FROM jsonb_each(service_to_purchase) AS v
                WHERE v.value = 'true'::jsonb
            )
            AND EXISTS (
                SELECT 1 FROM product_supplierinfo psi
                WHERE psi.product_tmpl_id = product_template.id
            )
        """,
    )
    # v20 _compute_purchase_ok forces purchase_ok on subcontracted services and
    # _compute_service_tracking drops 'subcontract' without it: keep both
    # stored values consistent, as the computes would.
    openupgrade.logged_query(
        cr,
        """
        UPDATE product_template
        SET purchase_ok = TRUE
        WHERE service_tracking = 'subcontract'
            AND purchase_ok IS NOT TRUE
        """,
    )
