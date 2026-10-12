# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # Cash on delivery provider: the redirect form of payment_custom is
    # removed in 20.0, providers use payment.generic_redirect_form; payment
    # methods now belong to a single provider (provider_id).
    openupgrade.load_data(env, "delivery", "20.0.1.0/noupdate_changes.xml")
