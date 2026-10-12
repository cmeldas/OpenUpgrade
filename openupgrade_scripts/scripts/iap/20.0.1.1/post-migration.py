# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


def _balance_amount(env):
    """iap.account.balance was a stored text ('12.5 Credits'), it is computed
    from the new float balance_amount in 20.0 (refreshed from the IAP server
    when the account is displayed). Seed balance_amount from the old text so
    that the balance is not shown as 0 until then."""
    if not openupgrade.column_exists(env.cr, "iap_account", "balance"):
        return
    openupgrade.logged_query(
        env.cr,
        r"""
        UPDATE iap_account
        SET balance_amount = substring(balance FROM '^\s*(-?[0-9]+(\.[0-9]+)?)')::float
        WHERE balance_amount IS NULL
            AND balance ~ '^\s*-?[0-9]+(\.[0-9]+)?'
        """,
    )


@openupgrade.migrate()
def migrate(env, version):
    _balance_amount(env)
