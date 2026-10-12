# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The translations of the body are kept on purpose: the 20.0 .po files
    # have no translation of the new text yet, the old translated text is
    # still valid (it only lacks the click & collect wording).
    openupgrade.load_data(env, "stock_sms", "20.0.1.0/noupdate_changes.xml")
