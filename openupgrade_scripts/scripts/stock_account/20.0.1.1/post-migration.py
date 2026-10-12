# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade
from psycopg2 import sql

# Description of a manual revaluation layer of 18.0:
# "Product value manually modified (from 0.0 to 83.93)", or translated
# ("... (od 0.0 do 83.93)"); group 1 is the old unit cost, group 2 the new one.
_revaluation_description_re = (
    r"\((?:from|od) (-?[0-9]+(?:\.[0-9]+)?) (?:to|do) (-?[0-9]+(?:\.[0-9]+)?)\)"
)


def _fill_product_value_cost_history(env):
    """Fill the new fields of the price history (product.value without move)
    like stock.product._change_standard_price() of 20.0 does:
    new_cost = value (unit cost), old_cost, quantity on hand at that date,
    old_value/new_value = cost * quantity.
    For the records repaired in pre-migration (former revaluation layers)
    the quantity on hand is the sum of the layers up to that date and the old
    cost is taken from the description, else from the previous record.
    """
    cr = env.cr
    backup = openupgrade.get_legacy_name("stock_valuation_layer")
    legacy_quantity = openupgrade.get_legacy_name("quantity")
    if openupgrade.table_exists(cr, backup) and openupgrade.column_exists(
        cr, "product_value", legacy_quantity
    ):
        openupgrade.logged_query(
            cr,
            sql.SQL(
                r"""
                WITH stock AS (
                    SELECT pv.id, SUM(svl.quantity) AS quantity
                    FROM product_value pv
                    JOIN {backup} svl
                        ON svl.product_id = pv.product_id
                        AND svl.company_id = pv.company_id
                        AND svl.lot_id IS NOT DISTINCT FROM pv.lot_id
                        AND svl.create_date <= pv.create_date
                    WHERE pv.move_id IS NULL AND pv.{legacy_quantity} IS NOT NULL
                    GROUP BY pv.id
                ), previous AS (
                    SELECT id, LAG(value) OVER (
                        PARTITION BY product_id, lot_id, company_id
                        ORDER BY date, id) AS cost
                    FROM product_value
                    WHERE move_id IS NULL
                ), described AS (
                    SELECT id, (regexp_match(description, %s))[1]::numeric AS cost
                    FROM product_value
                    WHERE move_id IS NULL AND {legacy_quantity} IS NOT NULL
                )
                UPDATE product_value pv
                SET new_cost = pv.value,
                    old_cost = COALESCE(described.cost, previous.cost, 0),
                    quantity = COALESCE(stock.quantity, 0),
                    new_value = pv.value * COALESCE(stock.quantity, 0),
                    old_value = COALESCE(described.cost, previous.cost, 0)
                        * COALESCE(stock.quantity, 0)
                FROM described
                JOIN previous ON previous.id = described.id
                LEFT JOIN stock ON stock.id = described.id
                WHERE pv.id = described.id AND pv.new_cost IS NULL
                """
            ).format(
                backup=sql.Identifier(backup),
                legacy_quantity=sql.Identifier(legacy_quantity),
            ),
            (_revaluation_description_re,),
        )
    # records created by 19.0 itself (no quantity was stored then): the old
    # cost is the previous price of the product, if any
    openupgrade.logged_query(
        cr,
        """
        UPDATE product_value pv
        SET new_cost = pv.value,
            old_cost = COALESCE(pv.old_cost, previous.cost, 0),
            quantity = COALESCE(pv.quantity, 0)
        FROM (
            SELECT id, LAG(value) OVER (
                PARTITION BY product_id, lot_id, company_id
                ORDER BY date, id) AS cost
            FROM product_value
            WHERE move_id IS NULL
        ) AS previous
        WHERE previous.id = pv.id AND pv.move_id IS NULL AND pv.new_cost IS NULL
        """,
    )


def _company_valuation_defaults(env):
    """res.company cost_method, inventory_valuation, account_stock_journal_id
    and account_stock_valuation_id are not stored anymore: 20.0 reads and
    writes them as the company ir.default of the product.category property
    fields. Create the ir.default from the 19.0 company columns where missing.
    """
    cr = env.cr
    mapping = [
        ("cost_method", "property_cost_method", True),
        ("inventory_valuation", "property_valuation", True),
        ("account_stock_journal_id", "property_stock_journal", False),
        ("account_stock_valuation_id", "property_stock_valuation_account_id", False),
    ]
    for column, field, is_selection in mapping:
        if not openupgrade.column_exists(cr, "res_company", column):
            continue
        value = (
            sql.SQL("to_jsonb(rc.{})::text") if is_selection else sql.SQL("rc.{}::text")
        ).format(sql.Identifier(column))
        openupgrade.logged_query(
            cr,
            sql.SQL(
                """
                INSERT INTO ir_default (
                    field_id, company_id, json_value,
                    create_uid, create_date, write_uid, write_date)
                SELECT imf.id, rc.id, {value},
                    1, now() AT TIME ZONE 'UTC', 1, now() AT TIME ZONE 'UTC'
                FROM res_company rc
                JOIN ir_model_fields imf
                    ON imf.model = 'product.category' AND imf.name = %s
                WHERE rc.{column} IS NOT NULL
                    AND NOT EXISTS (
                        SELECT 1 FROM ir_default idf
                        WHERE idf.field_id = imf.id AND idf.company_id = rc.id
                            AND idf.user_id IS NULL AND idf.condition IS NULL
                    )
                """
            ).format(value=value, column=sql.Identifier(column)),
            (field,),
        )


def _stock_valuation_closings(env):
    """19.0 remembered the stock valuation closing entries of a company in the
    ir.config_parameter '<company_id>.stock_valuation_closing_ids', 20.0 flags
    them (account.move.inventory_closing) and stores closing_datetime.
    """
    cr = env.cr
    if not openupgrade.column_exists(cr, "account_move", "inventory_closing"):
        return
    openupgrade.logged_query(
        cr,
        r"""
        UPDATE account_move am
        SET inventory_closing = TRUE,
            closing_datetime = COALESCE(
                am.closing_datetime, am.date + time '23:59:59.999999')
        FROM ir_config_parameter icp,
            LATERAL unnest(string_to_array(icp.value, ',')) AS closing(move_id)
        WHERE icp.key ~ '^[0-9]+\.stock_valuation_closing_ids$'
            AND closing.move_id ~ '^[0-9]+$'
            AND am.id = closing.move_id::integer
            AND am.company_id = split_part(icp.key, '.', 1)::integer
        """,
    )


def _drop_duplicated_valuation_cron(env):
    """Safety net for the XML-IDs moved to account in product pre-migration:
    if account still created its own records, drop the orphaned old ones.
    """
    cr = env.cr
    xmlids = []
    for name in (
        "ir_cron_post_stock_valuation",
        "ir_cron_post_stock_valuation_ir_actions_server",
        "action_report_stock_valuation",
    ):
        cr.execute(
            """
            SELECT 1 FROM ir_model_data old
            JOIN ir_model_data new ON new.module = 'account' AND new.name = old.name
            WHERE old.module = 'stock_account' AND old.name = %s
                AND old.res_id != new.res_id
            """,
            (name,),
        )
        if cr.fetchone():
            xmlids.append(f"stock_account.{name}")
    if xmlids:
        openupgrade.delete_records_safely_by_xml_id(env, xmlids)


@openupgrade.migrate()
def migrate(env, version):
    _fill_product_value_cost_history(env)
    _company_valuation_defaults(env)
    _stock_valuation_closings(env)
    _drop_duplicated_valuation_cron(env)
