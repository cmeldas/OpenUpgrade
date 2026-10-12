# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # onboarding to-do template rewritten in v20 (noupdate)
    openupgrade.load_data(env, "project_todo", "20.0.1.0/noupdate_changes.xml")
