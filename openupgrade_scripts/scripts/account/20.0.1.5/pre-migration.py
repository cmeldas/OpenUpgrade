# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_renamed_xmlids = [
    # stock valuation closing moved from stock_account to account. account is
    # loaded before stock_account, so the records must be taken over before
    # the data of account is loaded, or it would create duplicates (the
    # pre-migration of product, loaded earlier, does the same: no-op then).
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

# noupdate="1" in stock_account (v19), regular data in account (v20)
_noupdate_switched_xmlids = [
    "ir_cron_post_stock_valuation",
    "ir_cron_post_stock_valuation_ir_actions_server",
]


def _account_group_backup(env):
    """account.group is removed. Some groups are converted to parent accounts
    in the post-migration (see there), the others are dropped: keep a full
    copy of the table, whatever happens later to account_group."""
    if not openupgrade.table_exists(env.cr, "account_group"):
        return
    legacy_table = openupgrade.get_legacy_name("account_group")
    if openupgrade.table_exists(env.cr, legacy_table):
        return
    openupgrade.logged_query(
        env.cr, f"CREATE TABLE {legacy_table} AS (SELECT * FROM account_group)"
    )


def _account_move_review_state(env):
    """account.move.checked (boolean) -> review_state (selection, required).

    Same mapping as v20 `set_moves_checked`: a posted move that was checked is
    'reviewed', a posted move that was not checked is still 'todo'. Moves that
    are not posted (checked is always False for them in v19) get the default
    'no_review'.
    """
    cr = env.cr
    if openupgrade.column_exists(cr, "account_move", "review_state"):
        return
    openupgrade.logged_query(
        cr, "ALTER TABLE account_move ADD COLUMN review_state VARCHAR"
    )
    if not openupgrade.column_exists(cr, "account_move", "checked"):
        openupgrade.logged_query(
            cr, "UPDATE account_move SET review_state = 'no_review'"
        )
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE account_move
           SET review_state = CASE
               WHEN state = 'posted' AND checked THEN 'reviewed'
               WHEN state = 'posted' THEN 'todo'
               ELSE 'no_review'
           END
        """,
    )


def _account_move_document_tax_mode(env):
    """New stored compute document_tax_mode, protected by the SQL constraint
    check_document_tax_mode_set (set on every invoice). Pre-fill it the way
    `_compute_document_tax_mode` does, so that the constraint can be created
    on the existing invoices: company.account_price_include for invoices,
    NULL for the other moves."""
    cr = env.cr
    if openupgrade.column_exists(cr, "account_move", "document_tax_mode"):
        return
    openupgrade.logged_query(
        cr, "ALTER TABLE account_move ADD COLUMN document_tax_mode VARCHAR"
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE account_move am
           SET document_tax_mode =
               COALESCE(rc.account_price_include, 'tax_excluded')
          FROM res_company rc
         WHERE rc.id = am.company_id
           AND am.move_type IN ('out_invoice', 'out_refund', 'out_receipt',
                                'in_invoice', 'in_refund', 'in_receipt')
        """,
    )


def _account_move_line_deductible_percentage(env):
    """deductible_amount (0-100, default 100) -> deductible_percentage
    (0-1, default 1)."""
    cr = env.cr
    if openupgrade.column_exists(cr, "account_move_line", "deductible_percentage"):
        return
    openupgrade.logged_query(
        cr,
        "ALTER TABLE account_move_line "
        "ADD COLUMN deductible_percentage DOUBLE PRECISION",
    )
    if openupgrade.column_exists(cr, "account_move_line", "deductible_amount"):
        openupgrade.logged_query(
            cr,
            """
            UPDATE account_move_line
               SET deductible_percentage = COALESCE(deductible_amount, 100.0) / 100.0
            """,
        )
    else:
        openupgrade.logged_query(
            cr, "UPDATE account_move_line SET deductible_percentage = 1.0"
        )


def _account_payment_state(env):
    """The payment states were shifted in v20 ("In Process" -> "Paid",
    "Paid" -> "Reconciled"): 'in_process' is now 'paid' and the former 'paid'
    is now 'reconciled' (compare _compute_state, action_post and
    action_validate of both versions; account.move.payment_state and
    is_matched follow the same shift).

    The state is NOT recomputed: v19 'paid' payments validated by hand
    (action_validate) without a reconciled liquidity line would go back to
    'paid' (= v19 'in_process'). v20 differs for payments on an asset_cash
    outstanding account: v19 set them to 'paid' when posted, v20 keeps them
    'paid' until the liquidity line is reconciled (and action_validate refuses
    them), so they are mapped to 'paid' unless that line is reconciled."""
    cr = env.cr
    legacy = openupgrade.get_legacy_name("state")
    if openupgrade.column_exists(cr, "account_payment", legacy):
        return
    openupgrade.copy_columns(cr, {"account_payment": [("state", None, None)]})
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE account_payment
           SET state = CASE {legacy}
               WHEN 'paid' THEN 'reconciled'
               WHEN 'in_process' THEN 'paid'
           END
         WHERE {legacy} IN ('paid', 'in_process')
        """,
    )
    # v20 never reconciles an asset_cash payment by hand: it is 'reconciled'
    # only once its liquidity line is fully reconciled
    openupgrade.logged_query(
        cr,
        f"""
        UPDATE account_payment p
           SET state = 'paid'
          FROM account_account aa
         WHERE aa.id = p.outstanding_account_id
           AND aa.account_type = 'asset_cash'
           AND p.{legacy} = 'paid'
           AND p.move_id IS NOT NULL
           AND EXISTS (
               SELECT 1
                 FROM account_move_line aml
                WHERE aml.move_id = p.move_id
                  AND aml.account_id = p.outstanding_account_id
                  AND aml.amount_residual != 0)
        """,
    )


def _account_reconcile_model(env):
    """rule_type, matching_order, payment_tolerance_type come back in v20 as
    fields of account.reconcile.model, but the columns still hold the values
    of v18 (the fields were removed in v19 and the columns kept).

    - the v18 'invoice_matching' models were archived by OpenUpgrade 19.0
      only after OCA/OpenUpgrade#5981: archive them here if that was missed;
    - 'invoice_matching' -> 'matching_rule' (trigger must be 'auto_reconcile',
      see `_check_matching_rules`), anything else -> 'reco_model';
    - payment_tolerance_type 'fixed_amount' -> 'amount';
    - payment_tolerance (new) from the v18 payment_tolerance_param.
    """
    cr = env.cr
    if not openupgrade.column_exists(cr, "account_reconcile_model", "rule_type"):
        return
    openupgrade.logged_query(
        cr,
        """
        UPDATE account_reconcile_model
           SET active = FALSE
         WHERE rule_type = 'invoice_matching' AND active
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE account_reconcile_model
           SET rule_type = 'matching_rule', trigger = 'auto_reconcile'
         WHERE rule_type = 'invoice_matching'
        """,
    )
    openupgrade.logged_query(
        cr,
        """
        UPDATE account_reconcile_model
           SET rule_type = 'reco_model'
         WHERE rule_type IS NULL
            OR rule_type NOT IN ('matching_rule', 'reco_model')
        """,
    )
    if openupgrade.column_exists(
        cr, "account_reconcile_model", "payment_tolerance_type"
    ):
        openupgrade.logged_query(
            cr,
            """
            UPDATE account_reconcile_model
               SET payment_tolerance_type = 'amount'
             WHERE payment_tolerance_type = 'fixed_amount'
            """,
        )
        openupgrade.logged_query(
            cr,
            """
            UPDATE account_reconcile_model
               SET payment_tolerance_type = 'percentage'
             WHERE payment_tolerance_type IS NULL
                OR payment_tolerance_type NOT IN ('amount', 'percentage')
            """,
        )
    if openupgrade.column_exists(
        cr, "account_reconcile_model", "matching_order"
    ):
        openupgrade.logged_query(
            cr,
            """
            UPDATE account_reconcile_model
               SET matching_order = 'old_first'
             WHERE matching_order IS NULL
                OR matching_order NOT IN ('new_first', 'old_first')
            """,
        )
    if not openupgrade.column_exists(
        cr, "account_reconcile_model", "payment_tolerance"
    ) and openupgrade.column_exists(
        cr, "account_reconcile_model", "payment_tolerance_param"
    ):
        openupgrade.logged_query(
            cr,
            "ALTER TABLE account_reconcile_model "
            "ADD COLUMN payment_tolerance DOUBLE PRECISION",
        )
        allow = (
            "allow_payment_tolerance"
            if openupgrade.column_exists(
                cr, "account_reconcile_model", "allow_payment_tolerance"
            )
            else "TRUE"
        )
        openupgrade.logged_query(
            cr,
            f"""
            UPDATE account_reconcile_model
               SET payment_tolerance = CASE
                   WHEN {allow} THEN GREATEST(COALESCE(payment_tolerance_param, 0), 0)
                   ELSE 0
               END
            """,
        )


def _account_report_active(env):
    """account.report.active is not stored anymore: it is computed from the
    company dependent active_selection, falling back on active_fallback."""
    cr = env.cr
    if openupgrade.column_exists(cr, "account_report", "active_fallback"):
        return
    openupgrade.logged_query(
        cr, "ALTER TABLE account_report ADD COLUMN active_fallback BOOLEAN"
    )
    openupgrade.logged_query(
        cr, "UPDATE account_report SET active_fallback = COALESCE(active, TRUE)"
    )


def _res_company_quick_edit_mode(env):
    """Quick encoding is now enabled by a boolean. In v19 it was enabled as
    soon as quick_edit_mode was set. quick_edit_mode itself now only tells
    which documents use it and has a default ('out_and_in_invoices'): fill it
    where it was empty, otherwise enabling the boolean later would do nothing
    until the user also picks a mode (`_compute_quick_edit_mode`)."""
    cr = env.cr
    if openupgrade.column_exists(cr, "res_company", "quick_edit_mode_enabled"):
        return
    openupgrade.logged_query(
        cr, "ALTER TABLE res_company ADD COLUMN quick_edit_mode_enabled BOOLEAN"
    )
    if openupgrade.column_exists(cr, "res_company", "quick_edit_mode"):
        openupgrade.logged_query(
            cr,
            """
            UPDATE res_company
               SET quick_edit_mode_enabled = quick_edit_mode IS NOT NULL
            """,
        )
        openupgrade.logged_query(
            cr,
            """
            UPDATE res_company
               SET quick_edit_mode = 'out_and_in_invoices'
             WHERE quick_edit_mode IS NULL
            """,
        )


def _drop_changed_indexes(env):
    """Odoo keeps an existing index without comment whatever its definition
    (`Index.apply_to_database`), and these indexes of v19 (or older) have
    none. Drop the ones whose definition changed so that v20 creates them:
    - _checked_idx (now on review_state);
    - _duplicate_bills_idx (+in_receipt);
    - account_move_made_gaps and
      account_move_line_journal_id_neg_amnt_residual_idx: still in the
      definition of older versions (other columns / an extra
      parent_state = 'posted' condition the v20 queries do not use).
    """
    for index in (
        "account_move_checked_idx",
        "account_move_duplicate_bills_idx",
        "account_move_made_gaps",
        "account_move_line_journal_id_neg_amnt_residual_idx",
    ):
        openupgrade.logged_query(env.cr, f"DROP INDEX IF EXISTS {index}")


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.rename_xmlids(env.cr, _renamed_xmlids)
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE ir_model_data
           SET noupdate = FALSE
         WHERE module = 'account' AND name IN %s
        """,
        (tuple(_noupdate_switched_xmlids),),
    )
    _account_group_backup(env)
    _account_move_review_state(env)
    _account_move_document_tax_mode(env)
    _account_move_line_deductible_percentage(env)
    _account_payment_state(env)
    _account_reconcile_model(env)
    _account_report_active(env)
    _res_company_quick_edit_mode(env)
    _drop_changed_indexes(env)
