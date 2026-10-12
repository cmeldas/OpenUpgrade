# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # New layout of the purchase e-mails and the RFQ subject. Translations
    # are kept on purpose (the 20.0 .po files have no translation of the new
    # subject, the old translated subject stays usable).
    openupgrade.load_data(env, "purchase", "20.0.1.2/noupdate_changes.xml")
