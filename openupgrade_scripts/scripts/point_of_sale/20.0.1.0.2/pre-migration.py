# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_renamed_fields = [
    (
        "pos.config",
        "pos_config",
        "auto_validate_terminal_payment",
        "auto_validate_electronic_payment",
    ),
    (
        "pos.config",
        "pos_config",
        "is_closing_entry_by_product",
        "use_closing_entry_by_product",
    ),
    ("pos.config", "pos_config", "is_header_or_footer", "use_header_or_footer"),
    ("pos.config", "pos_config", "is_order_printer", "use_order_printer"),
    # same relation table (pos_config_printer_rel) in both versions
    ("pos.config", "pos_config", "printer_ids", "preparation_printer_ids"),
    ("pos.printer", "pos_printer", "epson_printer_ip", "printer_ip"),
]

# Columns whose values are used by the post-migration after the 20.0 ORM has
# dropped the field. The framework never drops columns, the rename only makes
# clear that the data is legacy.
_legacy_columns = {
    "pos_session": [
        ("move_id", None),
        ("cash_journal_id", None),
        ("cash_register_balance_start", None),
        ("cash_register_balance_end_real", None),
        ("cash_real_transaction", None),
        ("rescue", None),
    ],
    "pos_config": [("invoice_journal_id", None)],
}

# Fields moved to the new auto-installed module pos_stock.
_pos_stock_moved_fields = {
    "pos.config": [
        "picking_policy",
        "picking_type_id",
        "route_id",
        "ship_later",
        "warehouse_id",
    ],
    "pos.order": [
        "failed_pickings",
        "picking_count",
        "picking_ids",
        "picking_type_id",
        "shipping_date",
        "stock_reference_ids",
    ],
    "pos.order.line": ["pack_lot_ids"],
    "pos.session": [
        "failed_pickings",
        "picking_count",
        "picking_ids",
        "update_stock_at_closing",
    ],
    "res.company": ["point_of_sale_update_stock_quantities"],
    "stock.picking": ["pos_order_id", "pos_session_id"],
    "stock.picking.type": ["has_stock_reports_to_print"],
    "stock.reference": ["pos_order_ids"],
    "stock.warehouse": ["pos_type_id"],
}


def _move_stock_part_to_pos_stock(env):
    """The stock integration of the point of sale is the new module pos_stock
    (auto_install, and pos_mrp now depends on it). Move the ownership of the
    model pos.pack.operation.lot, of the moved fields, of their selection
    values and of the m2m relation, so that nothing is removed at the end of
    the update of point_of_sale."""
    cr = env.cr
    openupgrade.update_module_moved_models(
        cr, "pos.pack.operation.lot", "point_of_sale", "pos_stock"
    )
    for model, fields in _pos_stock_moved_fields.items():
        openupgrade.update_module_moved_fields(
            cr, model, fields, "point_of_sale", "pos_stock"
        )
        openupgrade.logged_query(
            cr,
            """
            UPDATE ir_model_data imd
               SET module = 'pos_stock'
              FROM ir_model_fields_selection sel
              JOIN ir_model_fields imf ON imf.id = sel.field_id
             WHERE imd.model = 'ir.model.fields.selection'
               AND imd.module = 'point_of_sale'
               AND imd.res_id = sel.id
               AND imf.model = %s
               AND imf.name IN %s
               AND NOT EXISTS (
                   SELECT 1 FROM ir_model_data other
                    WHERE other.module = 'pos_stock' AND other.name = imd.name
               )
            """,
            (model, tuple(fields)),
        )
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_model_relation imr
           SET module = new.id
          FROM ir_module_module old, ir_module_module new
         WHERE imr.module = old.id
           AND old.name = 'point_of_sale'
           AND new.name = 'pos_stock'
           AND imr.name = 'stock_reference_pos_order_rel'
        """,
    )


def _pos_config_journals(env):
    """v19: journal_id = journal of the session closing entries ('general' or
    'sale'), invoice_journal_id = journal of the invoices.
    v20: journal_id = journal of the invoices, closing_journal_id = journal of
    the session closing receipts (required, computed and stored; its compute
    would create a new journal 'POSC').

    Keep both journals of every config: closing_journal_id takes the former
    journal_id, journal_id takes the former invoice_journal_id. The closing
    journal is switched to type 'sale' by _closing_journals_to_sale()."""
    cr = env.cr
    if openupgrade.column_exists(cr, "pos_config", "closing_journal_id"):
        return
    openupgrade.copy_columns(cr, {"pos_config": [("journal_id", None, None)]})
    openupgrade.add_columns(
        env,
        [("pos.config", "closing_journal_id", "many2one", None, "pos_config")],
    )
    openupgrade.logged_query(
        cr,
        "UPDATE pos_config SET closing_journal_id = journal_id "
        "WHERE journal_id IS NOT NULL",
    )
    legacy_invoice_journal = openupgrade.get_legacy_name("invoice_journal_id")
    if openupgrade.column_exists(cr, "pos_config", legacy_invoice_journal):
        openupgrade.logged_query(
            cr,
            f"""
            UPDATE pos_config
               SET journal_id = {legacy_invoice_journal}
             WHERE {legacy_invoice_journal} IS NOT NULL
            """,
        )


def _closing_journals_to_sale(env):
    """The v20 session closing posts customer invoices / credit notes
    (out_invoice / out_refund) in closing_journal_id, which therefore has to be
    a 'sale' journal (the v19 point of sale journal is usually 'general').
    Besides, every closing calls _ensure_company_account_journal(), which
    looks for a 'sale' journal with code 'POSS' and creates one when missing:
    a 'general' journal already using the code would break the closing on the
    unique (company_id, code) constraint. Switch such journals to 'sale',
    except journals used by a payment method (blocked by a constraint in
    the ORM, cash/bank journals anyway)."""
    env.cr.execute(
        """
        SELECT aj.id
          FROM account_journal aj
         WHERE aj.type = 'general'
           AND (aj.id IN (SELECT closing_journal_id FROM pos_config)
                OR (aj.code = 'POSS'
                    AND aj.company_id IN (SELECT company_id FROM pos_config)))
           AND NOT EXISTS (SELECT 1 FROM pos_payment_method pm
                            WHERE pm.journal_id = aj.id)
        """
    )
    journal_ids = [row[0] for row in env.cr.fetchall()]
    if not journal_ids:
        return
    # SQL on purpose, and before any code of v20 can call
    # _ensure_company_account_journal(): an ORM write would recompute
    # account.move.line.no_followup (True for 'general' journals) on every past
    # closing entry and would create a mail alias the journal never had.
    openupgrade.logged_query(
        env.cr,
        "UPDATE account_journal SET type = 'sale' WHERE id IN %s",
        (tuple(journal_ids),),
    )
    # A sale journal has a default income account (set by
    # account.journal._fill_missing_values for new ones, required in the
    # journal form): take the one of another sale journal of the company.
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE account_journal aj
           SET default_account_id = (
               SELECT other.default_account_id
                 FROM account_journal other
                WHERE other.company_id = aj.company_id
                  AND other.type = 'sale'
                  AND other.default_account_id IS NOT NULL
                  AND other.id NOT IN %s
                ORDER BY other.sequence, other.id
                LIMIT 1
           )
         WHERE aj.id IN %s
           AND aj.default_account_id IS NULL
        """,
        (tuple(journal_ids), tuple(journal_ids)),
    )


def _pos_config_new_fields(env):
    """- session_closing_mode: v19 posted the accounting of a session only
      when it was closed. Keep that behaviour ('closing') instead of the v20
      default 'daily', which would let the cron post partial receipts of open
      sessions every night.
    - preparation_devices: v20 toggle shown above the preparation printers,
      v19 only had is_order_printer (renamed to use_order_printer)."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "pos_config", "session_closing_mode"):
        openupgrade.add_columns(
            env,
            [
                (
                    "pos.config",
                    "session_closing_mode",
                    "selection",
                    "closing",
                    "pos_config",
                )
            ],
        )
    if not openupgrade.column_exists(cr, "pos_config", "preparation_devices"):
        openupgrade.add_columns(
            env,
            [
                (
                    "pos.config",
                    "preparation_devices",
                    "boolean",
                    None,
                    "pos_config",
                )
            ],
        )
        openupgrade.logged_query(
            cr,
            "UPDATE pos_config SET preparation_devices = TRUE "
            "WHERE use_order_printer",
        )


def _pos_payment_method(env):
    """- type: computed and not stored in v19 (from the journal type, 'online'
      for pos_online_payment), stored and required in v20.
    - payment_method_type: selection value 'qr_code' is 'bank_qr_code' in v20.
    - use_payment_terminal -> payment_provider (same provider keys).
    - company_id is required now.
    - image: stored Image in v19, computed in v20 from the new stored
      custom_image."""
    cr = env.cr
    if not openupgrade.column_exists(cr, "pos_payment_method", "type"):
        openupgrade.add_columns(
            env,
            [
                (
                    "pos.payment.method",
                    "type",
                    "selection",
                    None,
                    "pos_payment_method",
                )
            ],
        )
        online = ""
        if openupgrade.column_exists(cr, "pos_payment_method", "is_online_payment"):
            online = "WHEN pm.is_online_payment THEN 'online'"
        openupgrade.logged_query(
            cr,
            f"""
            UPDATE pos_payment_method pm
               SET type = CASE
                   {online}
                   WHEN aj.type IN ('cash', 'bank') THEN aj.type
                   ELSE 'pay_later'
               END
              FROM pos_payment_method pm_j
              LEFT JOIN account_journal aj ON aj.id = pm_j.journal_id
             WHERE pm_j.id = pm.id
            """,
        )
    openupgrade.logged_query(
        cr,
        """
        UPDATE pos_payment_method
           SET payment_method_type = 'bank_qr_code'
         WHERE payment_method_type = 'qr_code'
        """,
    )
    if not openupgrade.column_exists(cr, "pos_payment_method", "payment_provider"):
        openupgrade.add_columns(
            env,
            [
                (
                    "pos.payment.method",
                    "payment_provider",
                    "selection",
                    None,
                    "pos_payment_method",
                )
            ],
        )
        if openupgrade.column_exists(
            cr, "pos_payment_method", "use_payment_terminal"
        ):
            openupgrade.logged_query(
                cr,
                """
                UPDATE pos_payment_method
                   SET payment_provider = use_payment_terminal
                 WHERE use_payment_terminal IS NOT NULL
                """,
            )
    openupgrade.logged_query(
        cr,
        """
        UPDATE pos_payment_method pm
           SET company_id = COALESCE(
               (SELECT aj.company_id FROM account_journal aj
                 WHERE aj.id = pm.journal_id),
               (SELECT min(id) FROM res_company)
           )
         WHERE pm.company_id IS NULL
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE ir_attachment
           SET res_field = 'custom_image'
         WHERE res_model = 'pos.payment.method'
           AND res_field = 'image'
        """,
    )


def _pos_payment_currency_rate(env):
    """pos.payment.currency_rate was related to pos_order_id.currency_rate in
    v19, it is a plain stored field in v20."""
    cr = env.cr
    if openupgrade.column_exists(cr, "pos_payment", "currency_rate"):
        return
    openupgrade.add_columns(
        env,
        [
            (
                "pos.payment",
                "currency_rate",
                "float",
                None,
                "pos_payment",
                "double precision",
            )
        ],
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE pos_payment pp
           SET currency_rate = po.currency_rate
          FROM pos_order po
         WHERE po.id = pp.pos_order_id
        """,
    )


def _fill_new_uuid(env):
    """product.attribute.custom.value.uuid is new with a lambda default: the
    ORM would give every existing row the same value."""
    cr = env.cr
    if openupgrade.column_exists(cr, "product_attribute_custom_value", "uuid"):
        return
    openupgrade.add_columns(
        env,
        [
            (
                "product.attribute.custom.value",
                "uuid",
                "char",
                None,
                "product_attribute_custom_value",
            )
        ],
    )
    openupgrade.logged_query(
        cr,
        "UPDATE product_attribute_custom_value SET uuid = gen_random_uuid()::text",
    )


def _pos_bill_name(env):
    """pos.bill.name is required now."""
    openupgrade.logged_query(
        env.cr,
        "UPDATE pos_bill SET name = value::text WHERE name IS NULL",
    )


@openupgrade.migrate()
def migrate(env, version):
    _move_stock_part_to_pos_stock(env)
    openupgrade.rename_fields(env, _renamed_fields)
    openupgrade.rename_columns(
        env.cr,
        {
            table: [
                spec
                for spec in specs
                if openupgrade.column_exists(env.cr, table, spec[0])
            ]
            for table, specs in _legacy_columns.items()
        },
    )
    _pos_config_journals(env)
    _closing_journals_to_sale(env)
    _pos_config_new_fields(env)
    _pos_payment_method(env)
    _pos_payment_currency_rate(env)
    _fill_new_uuid(env)
    _pos_bill_name(env)
