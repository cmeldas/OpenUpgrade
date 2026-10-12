# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # only the group comments: ir.access records are recreated from the v20
    # data, deactivations are transferred by base (end-migration)
    openupgrade.load_data(env, "hr_timesheet", "20.0.1.0/noupdate_changes.xml")
