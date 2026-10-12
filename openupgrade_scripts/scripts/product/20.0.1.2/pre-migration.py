# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_renamed_fields = [
    ("product.supplierinfo", "product_supplierinfo", "product_uom_id", "uom_id"),
]

# Records of stock_account that live in account in 20.0. account is updated
# before stock_account (it is one of its dependencies), so the XML-IDs have to
# be moved before account loads its data, otherwise account creates duplicates
# (a second valuation closing cron) and the old ones stay orphaned. product is
# a dependency of account, which makes its pre-migration the last safe place.
_stock_account_xmlids_to_account = [
    (
        "stock_account.ir_cron_post_stock_valuation",
        "account.ir_cron_post_stock_valuation",
    ),
    (
        "stock_account.ir_cron_post_stock_valuation_ir_actions_server",
        "account.ir_cron_post_stock_valuation_ir_actions_server",
    ),
    (
        "stock_account.action_report_stock_valuation",
        "account.action_report_stock_valuation",
    ),
]


def _move_stock_account_records_to_account(env):
    openupgrade.rename_xmlids(env.cr, _stock_account_xmlids_to_account)
    # noupdate switched: account defines the cron outside of a noupdate block
    openupgrade.set_xml_ids_noupdate_value(
        env,
        "account",
        [
            "ir_cron_post_stock_valuation",
            "ir_cron_post_stock_valuation_ir_actions_server",
        ],
        False,
    )


def _website_base_unit_to_product_base_unit(env):
    """website.base.unit (website_sale) is product.base.unit (product) in 20.0.

    product.template/product.product base_unit_id now point to the new model
    and are defined by product, so the model and its table must be renamed
    before product is updated. Otherwise the ORM creates an empty
    product_base_unit table and adding the foreign key on base_unit_id fails
    on the existing values.
    """
    cr = env.cr
    if not openupgrade.table_exists(cr, "website_base_unit") or (
        openupgrade.table_exists(cr, "product_base_unit")
    ):
        return
    openupgrade.rename_models(cr, [("website.base.unit", "product.base.unit")])
    openupgrade.rename_tables(cr, [("website_base_unit", "product_base_unit")])
    # rename_tables renamed the constraints in PostgreSQL, keep their
    # ir.model.constraint records in sync
    openupgrade.logged_query(
        cr,
        r"""
        UPDATE ir_model_constraint
        SET name = 'product_base_unit' || substr(name, length('website_base_unit') + 1)
        WHERE name LIKE 'website\_base\_unit\_%%'
        """,
    )
    openupgrade.update_module_moved_models(
        cr, "product.base.unit", "website_sale", "product"
    )
    openupgrade.rename_xmlids(
        cr,
        [
            ("website_sale.base_unit_action", "product.base_unit_action"),
            # 9 users in this group in production, keep the record
            ("website_sale.group_show_uom_price", "product.group_show_uom_price"),
        ],
    )
    # noupdate switched: product declares the group outside of a noupdate block
    openupgrade.set_xml_ids_noupdate_value(
        env, "product", ["group_show_uom_price"], False
    )


def _pricelist_item_compute_price(env):
    """compute_price 'percentage' and 'formula' are gone, 20.0 has 'discount'
    and 'markup', both computed from price_discount (price_markup is just
    -price_discount and is not stored anymore).
    """
    cr = env.cr
    legacy = openupgrade.get_legacy_name("compute_price")
    if not openupgrade.column_exists(cr, "product_pricelist_item", legacy):
        openupgrade.copy_columns(
            cr, {"product_pricelist_item": [("compute_price", None, None)]}
        )
    if openupgrade.column_exists(cr, "product_pricelist_item", "percent_price"):
        # 19.0 only applied percent_price for this type; rounding, surcharge
        # and margins were ignored, but are applied for 'discount' in 20.0
        openupgrade.logged_query(
            cr,
            """
            UPDATE product_pricelist_item
            SET compute_price = 'discount',
                price_discount = COALESCE(percent_price, 0),
                price_round = 0,
                price_surcharge = 0,
                price_min_margin = 0,
                price_max_margin = 0
            WHERE compute_price = 'percentage'
            """,
        )
    if openupgrade.column_exists(cr, "product_pricelist_item", "price_markup"):
        # 19.0 used -price_markup instead of price_discount on the cost base
        openupgrade.logged_query(
            cr,
            """
            UPDATE product_pricelist_item
            SET price_discount = -COALESCE(price_markup, 0)
            WHERE compute_price = 'formula' AND base = 'standard_price'
            """,
        )
    openupgrade.logged_query(
        cr,
        """
        UPDATE product_pricelist_item
        SET compute_price = CASE
            WHEN COALESCE(price_discount, 0) < 0 THEN 'markup' ELSE 'discount' END
        WHERE compute_price = 'formula'
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _move_stock_account_records_to_account(env)
    _website_base_unit_to_product_base_unit(env)
    openupgrade.rename_fields(env, _renamed_fields)
    _pricelist_item_compute_price(env)
