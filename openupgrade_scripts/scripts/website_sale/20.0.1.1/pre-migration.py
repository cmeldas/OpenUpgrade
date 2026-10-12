# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

_logger = logging.getLogger(__name__)

# (old column, new column) on table website: same meaning, new name
_website_renamed_columns = [
    ("prevent_zero_price_sale", "prevent_sale"),
    ("contact_us_button_url", "contact_us_link_url"),
    ("send_abandoned_cart_email", "send_abandoned_cart_followup"),
]

# removed fields, kept as legacy columns (some are mapped in post-migration)
_legacy_columns = {
    "website": ["add_to_cart_action", "enabled_gmc_src"],
    "product_public_category": [
        "show_category_title",
        "show_category_description",
        "align_category_content",
    ],
    "product_document": ["shown_on_product_page"],
}

_xmlids_renames = [
    # "Newest Products" became "Newest arrivals" (same filter, sorted on the
    # publication date instead of the creation date): keep the records, they
    # may be referenced by dynamic snippets (data-filter-id)
    (
        "website_sale.dynamic_snippet_newest_products_filter",
        "website_sale.dynamic_snippet_newest_arrivals_filter",
    ),
    (
        "website_sale.dynamic_filter_newest_products",
        "website_sale.dynamic_filter_newest_arrivals",
    ),
    # donation snippet moved from website_payment
    (
        "website_payment.s_donation_000_scss",
        "website_sale.s_donation_000_scss",
    ),
    # In 19.0 `website_sale.ir_cron_send_availability_email` was the
    # abandoned cart cron and the back in stock cron was
    # `website_sale_stock.ir_cron_send_availability_email`. 20.0 names them
    # `website_sale.ir_cron_send_abandoned_email` and
    # `website_sale.ir_cron_send_availability_email`, in a noupdate file:
    # without the renames the old abandoned cart cron would keep the
    # availability XML-ID (not updated, obsolete code) and the back in stock
    # cron would be removed with website_sale_stock data. Order matters.
    (
        "website_sale.ir_cron_send_availability_email",
        "website_sale.ir_cron_send_abandoned_email",
    ),
    (
        "website_sale.ir_cron_send_availability_email_ir_actions_server",
        "website_sale.ir_cron_send_abandoned_email_ir_actions_server",
    ),
    (
        "website_sale_stock.ir_cron_send_availability_email",
        "website_sale.ir_cron_send_availability_email",
    ),
    (
        "website_sale_stock.ir_cron_send_availability_email_ir_actions_server",
        "website_sale.ir_cron_send_availability_email_ir_actions_server",
    ),
]


def rename_website_columns(cr):
    for old, new in _website_renamed_columns:
        if openupgrade.column_exists(
            cr, "website", old
        ) and not openupgrade.column_exists(cr, "website", new):
            openupgrade.rename_columns(cr, {"website": [(old, new)]})


def rename_legacy_columns(cr):
    spec = {}
    for table, columns in _legacy_columns.items():
        for column in columns:
            if openupgrade.column_exists(
                cr, table, column
            ) and not openupgrade.column_exists(
                cr, table, openupgrade.get_legacy_name(column)
            ):
                spec.setdefault(table, []).append((column, None))
    if spec:
        openupgrade.rename_columns(cr, spec)


def rename_xmlids_if_free(cr, xmlids_spec):
    """`openupgrade.rename_xmlids`, skipping the renames whose target XML-ID
    already exists (a rerun, or a database where 20.0 data was loaded):
    renaming onto it would violate the unique key of ir_model_data. The
    renames are applied one by one, in order, as a later one may target the
    XML-ID freed by an earlier one.
    """
    for old, new in xmlids_spec:
        cr.execute(
            "SELECT 1 FROM ir_model_data WHERE module = %s AND name = %s",
            tuple(new.split(".", 1)),
        )
        if not cr.fetchone():
            openupgrade.rename_xmlids(cr, [(old, new)])


def check_base_unit_moved(cr):
    """`website.base.unit` became `product.base.unit` (module product, which is
    updated before website_sale). The model and its table must be renamed in
    the pre-migration of product; warn if the data was left behind.
    """
    if openupgrade.table_exists(cr, "website_base_unit"):
        cr.execute("SELECT count(*) FROM website_base_unit")
        count = cr.fetchone()[0]
        if count:
            _logger.warning(
                "%s website.base.unit records were not moved to "
                "product.base.unit by the product pre-migration",
                count,
            )


def make_suggested_products_list_primary(cr):
    """`website_sale.suggested_products_list` was an extension of `cart_lines`
    in 19 and is a standalone template called by `cart_lines` in 20. The XML
    loader does not reset `inherit_id` when the attribute disappears, so the
    standard view would stay an extension with a 20 arch and break the cart.
    Website-specific (COW) copies still hold a 19 xpath arch that cannot be
    applied any more: archive them (kept for manual review) instead of
    deleting user customizations.
    """
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_ui_view v
        SET inherit_id = NULL, mode = 'primary'
        FROM ir_model_data imd
        WHERE imd.model = 'ir.ui.view' AND imd.res_id = v.id
            AND imd.module = 'website_sale'
            AND imd.name = 'suggested_products_list'
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_ui_view
        SET inherit_id = NULL, mode = 'primary', active = FALSE
        WHERE key = 'website_sale.suggested_products_list'
            AND website_id IS NOT NULL
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    cr = env.cr
    rename_website_columns(cr)
    rename_legacy_columns(cr)
    rename_xmlids_if_free(cr, _xmlids_renames)
    check_base_unit_moved(cr)
    make_suggested_products_list_primary(cr)
