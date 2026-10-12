# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The XML-ID is an ir.actions.server in 20.0 (was ir.actions.act_window):
    # loading the data on the old record raises "found record of different
    # model", drop the old action first
    openupgrade.delete_records_safely_by_xml_id(
        env, ["mass_mailing.mailing_list_merge_action"]
    )
