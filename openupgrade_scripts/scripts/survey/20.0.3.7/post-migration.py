# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # survey enables its KPIs on the default digest (noupdate record of the
    # digest module) like a fresh install does
    openupgrade.load_data(env, "survey", "20.0.3.7/noupdate_changes.xml")
