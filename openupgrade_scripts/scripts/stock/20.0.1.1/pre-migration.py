# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_renamed_fields = [
    ("stock.move", "stock_move", "product_uom", "uom_id"),
    ("stock.move", "stock_move", "location_final_id", "forecasted_location_id"),
    ("stock.move.line", "stock_move_line", "product_uom_id", "uom_id"),
]


def _column_type(cr, table, column):
    cr.execute(
        """
        SELECT data_type FROM information_schema.columns
        WHERE table_schema = current_schema()
            AND table_name = %s AND column_name = %s
        """,
        (table, column),
    )
    row = cr.fetchone()
    return row[0] if row else None


def _stock_move_quantity_product_uom(env):
    """New stored compute: precreate and fill it instead of letting the ORM
    convert the quantity of every move one by one.
    Same formula as uom.uom._compute_quantity() with HALF-UP rounding to the
    'Product Unit' precision.
    """
    cr = env.cr
    if openupgrade.column_exists(cr, "stock_move", "quantity_product_uom"):
        return
    openupgrade.add_columns(
        env,
        [
            (
                "stock.move",
                "quantity_product_uom",
                "float",
                None,
                "stock_move",
                "numeric",
            )
        ],
    )
    cr.execute("SELECT digits FROM decimal_precision WHERE name = 'Product Unit'")
    row = cr.fetchone()
    digits = row[0] if row else 2
    openupgrade.logged_query(
        cr,
        """
        UPDATE stock_move sm
        SET quantity_product_uom = CASE
            WHEN sm.uom_id = pt.uom_id OR sm.quantity IS NULL OR sm.quantity = 0
            THEN sm.quantity
            ELSE round(
                (sm.quantity * move_uom.factor / product_uom.factor)::numeric, %s)
            END
        FROM product_product pp
        JOIN product_template pt ON pt.id = pp.product_tmpl_id
        JOIN uom_uom product_uom ON product_uom.id = pt.uom_id,
        uom_uom move_uom
        WHERE pp.id = sm.product_id AND move_uom.id = sm.uom_id
        """,
        (digits,),
    )


def _stock_scrap_to_stock_move(env):
    """stock.scrap is gone, a scrap is a stock.move with is_scrap in 20.0.

    Every done scrap of 19.0 already has its move (stock_move.scrap_id), which
    carries the product, quantity, locations, lot, package, owner and the
    reference (the scrap name). Flag those moves and copy what only lived on
    the scrap. The scrap reason tags (many2many) are copied in post-migration,
    when the new relation table exists. The table stock_scrap and the column
    stock_move.scrap_id are kept as the mapping old scrap id -> move id.
    """
    cr = env.cr
    openupgrade.add_columns(
        env,
        [
            ("stock.move", "is_scrap", "boolean", False, "stock_move"),
            ("stock.move", "should_replenish_scrapped", "boolean", False, "stock_move"),
        ],
    )
    if not openupgrade.table_exists(cr, "stock_scrap") or not (
        openupgrade.column_exists(cr, "stock_move", "scrap_id")
    ):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE stock_move sm
        SET is_scrap = TRUE,
            should_replenish_scrapped = COALESCE(ss.should_replenish, FALSE)
        FROM stock_scrap ss
        WHERE sm.scrap_id = ss.id AND sm.is_scrap IS NOT TRUE
        """,
    )
    # Scraps that were never validated have no move and cannot be represented
    # in 20.0 (a draft scrap is just a draft move there); keep them in the
    # legacy table and report them.
    cr.execute(
        """
        SELECT count(*) FROM stock_scrap ss
        WHERE NOT EXISTS (SELECT 1 FROM stock_move sm WHERE sm.scrap_id = ss.id)
        """
    )
    orphan_scraps = cr.fetchone()[0]
    if orphan_scraps:
        openupgrade.message(
            cr,
            "stock",
            "stock_scrap",
            False,
            "%s scraps without a stock move were not converted, they are only"
            " kept in the legacy table stock_scrap",
            orphan_scraps,
        )


def _scrap_reason_tag_color(env):
    """color: char '#RRGGBB' -> integer (RGB value, 20.0 default 0x3C3C3C).
    The automatic cast of the ORM fails on these values and stops the update.
    """
    cr = env.cr
    if _column_type(cr, "stock_scrap_reason_tag", "color") in (None, "integer"):
        return
    openupgrade.copy_columns(cr, {"stock_scrap_reason_tag": [("color", None, None)]})
    openupgrade.logged_query(
        cr,
        r"""
        ALTER TABLE stock_scrap_reason_tag
        ALTER COLUMN color DROP DEFAULT,
        ALTER COLUMN color TYPE integer USING (
            CASE WHEN color ~* '^#?[0-9a-f]{6}$'
                THEN ('x' || right(color, 6))::bit(24)::integer
                ELSE 3947580
            END
        )
        """,
    )


def _product_template_tracking(env):
    """tracking: 'none' (By Quantity) is removed, it is False in 20.0
    (storable products without tracking, see store_by). The field is not
    required anymore and is False for non storable products.
    """
    cr = env.cr
    legacy = openupgrade.get_legacy_name("tracking")
    if not openupgrade.column_exists(cr, "product_template", legacy):
        openupgrade.copy_columns(cr, {"product_template": [("tracking", None, None)]})
    openupgrade.logged_query(
        cr, "ALTER TABLE product_template ALTER COLUMN tracking DROP NOT NULL"
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE product_template
        SET tracking = NULL
        WHERE tracking = 'none'
            OR (tracking IS NOT NULL AND is_storable IS NOT TRUE)
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.rename_fields(env, _renamed_fields)
    _stock_move_quantity_product_uom(env)
    _stock_scrap_to_stock_move(env)
    _scrap_reason_tag_color(env)
    _product_template_tracking(env)
