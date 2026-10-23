# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import logging

from openupgradelib import openupgrade

from odoo import Command

_logger = logging.getLogger(__name__)


def _link_session_moves(env):
    """pos.session.move_id (one closing entry per session) is gone; v20 keeps
    the closing receipts of a session in sale_move_ids / refund_move_ids
    (inverse account.move.pos_session_sales_id / _refunds_id). A v19 closing
    entry holds sales and refunds together, it becomes the sales entry of the
    session. This keeps the session <-> entry link used by the session smart
    buttons, account.move.pos_session_ids and the invoicing of an order of a
    closed session (_generate_invoice_after_session_closing)."""
    legacy_move = openupgrade.get_legacy_name("move_id")
    if not openupgrade.column_exists(env.cr, "pos_session", legacy_move):
        return
    openupgrade.logged_query(
        env.cr,
        f"""
        UPDATE account_move am
           SET pos_session_sales_id = ps.id
          FROM pos_session ps
         WHERE ps.{legacy_move} = am.id
           AND am.pos_session_sales_id IS NULL
        """,
    )


def _pos_config_default_partner(env):
    """default_partner_id is new, required, with a default that looks for the
    noupdate record point_of_sale.default_session_closing_partner and creates
    an anonymous 'Odoo POS' partner when the record is not there yet. That is
    the case when the column is initialised, before the data of point_of_sale
    is loaded, so the existing configs point to a duplicate without XML-ID.
    Point them to the record of the module (setting its receivable account
    per company as the default does) and drop the duplicates."""
    partner = env.ref(
        "point_of_sale.default_session_closing_partner", raise_if_not_found=False
    )
    if not partner:
        return
    configs = env["pos.config"].with_context(active_test=False).search([])
    for company in configs.company_id:
        env["pos.config"].with_company(company).sudo()._get_or_create_default_partner()
    duplicates = (
        configs.default_partner_id.with_context(active_test=False).filtered(
            lambda p: p != partner
            and not p.active
            and p.name == partner.name
            and not p.get_external_id().get(p.id)
        )
    )
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE pos_config
           SET default_partner_id = %s
         WHERE default_partner_id IS NULL OR default_partner_id IN %s
        """,
        (partner.id, tuple(duplicates.ids) or (0,)),
    )
    env["pos.config"].invalidate_model(["default_partner_id"])
    for duplicate in duplicates:
        try:
            with env.cr.savepoint():
                duplicate.unlink()
        except Exception as error:  # noqa: BLE001
            _logger.warning(
                "point_of_sale: could not delete duplicate partner %s: %s",
                duplicate.id,
                error,
            )


def _pos_printers_use_lna(env):
    """v19 chose the protocol of ePoS printers globally: http when the system
    parameter point_of_sale.use_lna is set, else the protocol of the POS page.
    v20 stores use_lna per printer and without it always calls https://, so a
    printer that worked from a POS opened over http (plain ePoS devices and
    ePoS bridges only listen on http) would stop printing. Use Local Network
    Access (http with the browser's local network permission) is the v20 way
    that works from http and https pages alike: enable it on all ePoS printers
    unless the v19 parameter explicitly said otherwise.
    """
    cr = env.cr
    cr.execute(
        "SELECT value FROM ir_config_parameter WHERE key = 'point_of_sale.use_lna'"
    )
    row = cr.fetchone()
    use_lna = (row[0] or "").strip().lower() not in ("0", "false", "") if row else True
    openupgrade.logged_query(
        cr,
        """
        UPDATE pos_printer SET use_lna = %s
         WHERE printer_type = 'epson_epos' AND use_lna IS NOT TRUE
        """,
        (use_lna,),
    )


def _pos_config_receipt_printers(env):
    """v19 printed receipts on an ePoS printer set on the config
    (epson_printer_ip, iface_cashdrawer); v20 uses a pos.printer of type
    'receipt' linked in receipt_printer_ids. Printing through an IoT box
    (iface_print_via_proxy / proxy_ip) has no Community equivalent in v20."""
    cr = env.cr
    if openupgrade.column_exists(cr, "pos_config", "epson_printer_ip"):
        cashdrawer = (
            "iface_cashdrawer"
            if openupgrade.column_exists(cr, "pos_config", "iface_cashdrawer")
            else "FALSE"
        )
        cr.execute(
            f"""
            SELECT id, epson_printer_ip, COALESCE({cashdrawer}, FALSE)
              FROM pos_config
             WHERE COALESCE(epson_printer_ip, '') != ''
            """
        )
        for config_id, printer_ip, use_cashdrawer in cr.fetchall():
            config = env["pos.config"].browse(config_id)
            printer = env["pos.printer"].create(
                {
                    "name": config.name,
                    "printer_type": "epson_epos",
                    "use_type": "receipt",
                    "printer_ip": printer_ip,
                    "use_cashdrawer": use_cashdrawer,
                }
            )
            openupgrade.logged_query(
                cr,
                """
                INSERT INTO pos_config_receipt_printer_rel (config_id, printer_id)
                VALUES (%s, %s) ON CONFLICT DO NOTHING
                """,
                (config_id, printer.id),
            )
            openupgrade.logged_query(
                cr,
                "UPDATE pos_config SET other_devices = TRUE WHERE id = %s",
                (config_id,),
            )
    if openupgrade.column_exists(cr, "pos_config", "iface_print_via_proxy"):
        cr.execute(
            """
            SELECT id FROM pos_config
             WHERE iface_print_via_proxy OR COALESCE(proxy_ip, '') != ''
            """
        )
        config_ids = [row[0] for row in cr.fetchall()]
        if config_ids:
            _logger.warning(
                "point_of_sale: pos.config %s used an IoT box (proxy_ip / "
                "iface_print_via_proxy / iface_cashdrawer); not migrated, "
                "configure a receipt printer manually",
                config_ids,
            )


def _pos_cash_statements(env):
    """v19 kept the cash register of a session in pos.session
    (cash_register_balance_start / _end_real, cash_journal_id) and the cash
    moves as statement lines without statement. v20 keeps them in an
    account.bank.statement per session (bank_statement_id) and takes the
    opening balance of a new session from the running balance of the cash
    journal (journal.current_statement_balance = last statement with lines +
    the lines after it). Without statements that is the sum of all cash lines
    ever posted, which does not match the drawer (v19 did not record the cash
    taken out between sessions), so the first v20 session would post a huge
    cash correction.

    1. Anchor per POS cash journal: a statement holding only the last posted
       statement line (outside of open sessions) whose ending balance is the
       amount the v19 point of sale expects in the drawer (opening balance of
       the open session, otherwise closing balance of the last closed one).
    2. Open sessions get their statement now (opening balance and their own
       cash lines), the v20 closing adds the cash sales and the difference.

    The balances of closed sessions stay in the legacy columns."""
    cr = env.cr
    journal_col = openupgrade.get_legacy_name("cash_journal_id")
    start_col = openupgrade.get_legacy_name("cash_register_balance_start")
    end_col = openupgrade.get_legacy_name("cash_register_balance_end_real")
    if not openupgrade.column_exists(cr, "pos_session", journal_col):
        return
    Statement = env["account.bank.statement"].sudo()
    StatementLine = env["account.bank.statement.line"].sudo()
    cr.execute(
        f"""
        SELECT id, {journal_col}, COALESCE({start_col}, 0)
          FROM pos_session
         WHERE state != 'closed'
           AND {journal_col} IS NOT NULL
           AND bank_statement_id IS NULL
         ORDER BY id
        """
    )
    open_sessions = cr.fetchall()
    open_session_ids = [row[0] for row in open_sessions]
    cr.execute(
        f"""
        SELECT {journal_col}, count(DISTINCT config_id)
          FROM pos_session
         WHERE {journal_col} IS NOT NULL
         GROUP BY 1
         ORDER BY 1
        """
    )
    for journal_id, config_count in cr.fetchall():
        if config_count > 1:
            _logger.warning(
                "point_of_sale: cash journal %s is shared by several points of "
                "sale, no opening balance statement created",
                journal_id,
            )
            continue
        if Statement.search_count(
            [("journal_id", "=", journal_id), ("first_line_index", "!=", False)]
        ):
            continue
        open_starts = [row[2] for row in open_sessions if row[1] == journal_id]
        if open_starts:
            expected = float(open_starts[0])
        else:
            cr.execute(
                f"""
                SELECT COALESCE({end_col}, 0)
                  FROM pos_session
                 WHERE {journal_col} = %s AND state = 'closed'
                 ORDER BY stop_at DESC NULLS LAST, id DESC
                 LIMIT 1
                """,
                (journal_id,),
            )
            row = cr.fetchone()
            if not row:
                continue
            expected = float(row[0])
        last_line = StatementLine.search(
            [
                ("journal_id", "=", journal_id),
                ("statement_id", "=", False),
                ("state", "=", "posted"),
                ("pos_session_id", "not in", open_session_ids),
            ],
            order="internal_index desc",
            limit=1,
        )
        if not last_line:
            if expected:
                _logger.warning(
                    "point_of_sale: cash journal %s has no statement line, the "
                    "next session will expect 0 instead of %s",
                    journal_id,
                    expected,
                )
            continue
        try:
            with cr.savepoint():
                statement = Statement.create(
                    {
                        "name": env._("Cash register balance before Odoo 20"),
                        "journal_id": journal_id,
                        "balance_start": expected - last_line.amount,
                        "balance_end_real": expected,
                        "line_ids": [Command.set(last_line.ids)],
                    }
                )
        except Exception as error:  # noqa: BLE001
            _logger.error(
                "point_of_sale: no opening balance statement for cash journal "
                "%s, the next session will expect a wrong amount: %s",
                journal_id,
                error,
            )
            continue
        _logger.info(
            "point_of_sale: cash journal %s anchored at %s (statement %s)",
            journal_id,
            expected,
            statement.id,
        )
    for session_id, journal_id, start in open_sessions:
        session = env["pos.session"].browse(session_id)
        lines = StatementLine.search(
            [
                ("pos_session_id", "=", session_id),
                ("journal_id", "=", journal_id),
                ("statement_id", "=", False),
            ]
        )
        statement = Statement.create(
            {
                "name": env._(
                    "Cash Statement for %(method_name)s in %(session)s",
                    method_name=env["account.journal"].browse(journal_id).name,
                    session=session.name,
                ),
                "journal_id": journal_id,
                "balance_start": float(start),
                "balance_end_real": float(start) + sum(lines.mapped("amount")),
                "line_ids": [Command.set(lines.ids)],
            }
        )
        openupgrade.logged_query(
            cr,
            "UPDATE pos_session SET bank_statement_id = %s WHERE id = %s",
            (statement.id, session_id),
        )
    env["pos.session"].invalidate_model(["bank_statement_id"])


def _pos_order_amount_difference(env):
    """pos.order.amount_difference (paid minus computed total, i.e. the cash
    rounding of the order) exists in v19 too, but v19 never filled it for
    orders coming from the POS UI. v20 sets it in _process_saved_order and the
    session closing relies on it: _get_rounding_method_for_invoice only adds
    the cash rounding line when an order has a non-zero difference, otherwise
    closing a session with rounded orders fails with "The entry is not
    balanced". Fill it for the migrated orders.
    """
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE pos_order
           SET amount_difference = amount_paid - amount_total
         WHERE COALESCE(amount_difference, 0) = 0
           AND abs(amount_paid - amount_total) > 0.000001
           AND state IN ('paid', 'done', 'invoiced')
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "point_of_sale", "20.0.1.0.2/noupdate_changes.xml")
    _link_session_moves(env)
    _pos_config_default_partner(env)
    _pos_config_receipt_printers(env)
    _pos_printers_use_lna(env)
    _pos_cash_statements(env)
    _pos_order_amount_difference(env)
