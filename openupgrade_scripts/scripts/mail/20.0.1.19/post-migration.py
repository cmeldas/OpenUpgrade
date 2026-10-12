# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "mail", "20.0.1.19/noupdate_changes.xml")
    # 'Discussions' is 'Messages' in 20.0: drop the old translations so that
    # the translation update following this script loads the new ones. The
    # other renamed subtype (mt_note 'Note' -> 'Notes') keeps its translations,
    # they still fit.
    openupgrade.delete_record_translations(env.cr, "mail", ["mt_comment"], ["name"])
