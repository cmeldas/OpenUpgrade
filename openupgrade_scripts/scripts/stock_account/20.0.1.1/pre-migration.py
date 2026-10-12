# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade
from psycopg2 import sql

# Columns of the 18.0 stock.valuation.layer that the first 19.0 version of
# this module's scripts left in product_value (OpenUpgrade before 8c3360d0dac,
# PR #5881). quantity and account_move_id are new fields of product.value in
# 20.0 with another meaning, the rest is kept as legacy data.
_svl_columns = [
    "quantity",
    "account_move_id",
    "unit_cost",
    "remaining_qty",
    "remaining_value",
    "stock_move_id",
    "stock_valuation_layer_id",
    "account_move_line_id",
    "categ_id",
    "price_diff_value",
]

# Description of a manual revaluation layer of 18.0:
# "Product value manually modified (from 0.0 to 83.93)", or translated
# ("... (od 0.0 do 83.93)"); group 1 is the old unit cost, group 2 the new one.
_revaluation_description_re = (
    r"\((?:from|od) (-?[0-9]+(?:\.[0-9]+)?) (?:to|do) (-?[0-9]+(?:\.[0-9]+)?)\)"
)


def _repair_product_value(env):
    """Repair product.value of DBs migrated to 19.0 with the old script, which
    renamed stock.valuation.layer to product.value instead of converting it.

    In 19.0 and 20.0 a product.value is a manual change: with a move it
    overrides the value of the move, without a move its value is the new
    *unit* cost of the product at that date. The renamed layers broke both:
    - layers of moves look like manual overrides of the move value (the value
      is already in stock_move.value, the 19.0 script summed the layers);
    - revaluation layers (no move) hold the total revaluation amount instead
      of the unit cost, which 20.0 replays as price history.
    The renamed table is recognised by the layer column quantity, which
    19.0 does not have. A DB migrated with the fixed 19.0 script kept
    stock_valuation_layer and is not touched.
    """
    cr = env.cr
    if not openupgrade.column_exists(
        cr, "product_value", "quantity"
    ) or not openupgrade.column_exists(cr, "product_value", "unit_cost"):
        return
    backup = openupgrade.get_legacy_name("stock_valuation_layer")
    if not openupgrade.table_exists(cr, backup):
        openupgrade.logged_query(
            cr,
            sql.SQL(
                """
                CREATE TABLE {} AS
                SELECT * FROM product_value WHERE quantity IS NOT NULL
                """
            ).format(sql.Identifier(backup)),
        )
    # 1. revaluations without move: total value -> unit cost, with the
    # algorithm of 8c3360d0dac (unit_cost, else running value / quantity of
    # the layers up to that date), else the new cost from the description
    # ("... (from 0.0 to 83.93)" / "... (od 0.0 do 83.93)").
    openupgrade.logged_query(
        cr,
        r"""
        WITH running AS (
            SELECT pv.id,
                SUM(prev.value) / NULLIF(SUM(prev.quantity), 0) AS unit_cost
            FROM product_value pv
            JOIN product_value prev
                ON prev.product_id = pv.product_id
                AND prev.company_id = pv.company_id
                AND prev.lot_id IS NOT DISTINCT FROM pv.lot_id
                AND prev.create_date <= pv.create_date
                AND prev.quantity IS NOT NULL
            WHERE pv.move_id IS NULL AND pv.quantity IS NOT NULL
            GROUP BY pv.id
        ), described AS (
            SELECT id, (regexp_match(description, %s))[2]::numeric AS unit_cost
            FROM product_value
            WHERE move_id IS NULL AND quantity IS NOT NULL
        )
        UPDATE product_value pv
        SET value = COALESCE(
            NULLIF(pv.unit_cost, 0), running.unit_cost, described.unit_cost, pv.value)
        FROM described
        LEFT JOIN running ON running.id = described.id
        WHERE pv.id = described.id
        """,
        (_revaluation_description_re,),
    )
    # 2. drop the foreign keys of the layer columns (they would block
    # deleting moves, journal entries or categories) and the ones of legacy
    # layer tables pointing to product_value (the 18.0 revaluation relation
    # cascades), before deleting rows, so that no legacy data is touched
    svl_columns = [
        column
        for column in _svl_columns
        if openupgrade.column_exists(cr, "product_value", column)
    ]
    cr.execute(
        """
        SELECT con.conrelid::regclass::text, con.conname
        FROM pg_constraint con
        JOIN pg_attribute att
            ON att.attrelid = con.conrelid AND att.attnum = ANY(con.conkey)
        WHERE con.contype = 'f'
            AND (
                (con.conrelid = 'product_value'::regclass
                    AND att.attname = ANY(%s))
                OR (con.confrelid = 'product_value'::regclass
                    AND con.conrelid != 'product_value'::regclass
                    AND att.attname = 'stock_valuation_layer_id')
            )
        """,
        (svl_columns,),
    )
    for table, constraint in cr.fetchall():
        openupgrade.logged_query(
            cr,
            sql.SQL("ALTER TABLE {} DROP CONSTRAINT IF EXISTS {}").format(
                sql.Identifier(table.strip('"')), sql.Identifier(constraint)
            ),
        )
    # 3. layers of moves are not manual values (the value stays in
    # stock_move.value)
    openupgrade.logged_query(
        cr,
        """
        DELETE FROM product_value
        WHERE move_id IS NOT NULL AND quantity IS NOT NULL
        """,
    )
    # 4. keep the layer columns as legacy, 20.0 creates its own quantity and
    # account_move_id with another meaning
    openupgrade.rename_columns(
        cr, {"product_value": [(column, None) for column in svl_columns]}
    )


def _stock_move_value_sign(env):
    """20.0 stores the value of outgoing moves as a negative amount, 19.0 as a
    positive one (moves migrated from 18.0 layers are already negative).
    """
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE stock_move
        SET value = -value
        WHERE is_out AND value > 0
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _repair_product_value(env)
    _stock_move_value_sign(env)
