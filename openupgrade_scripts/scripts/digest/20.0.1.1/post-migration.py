# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The noupdate QWeb templates of the digest email changed in 20.0 (date in
    # the title, KPI icons and tips, cross company KPIs). The data file only
    # holds these four templates: reload them. The translations of the terms
    # that did not change are kept by the loader, the others come from the
    # translation update that follows.
    openupgrade.load_data(env, "digest", "data/digest_data.xml")
