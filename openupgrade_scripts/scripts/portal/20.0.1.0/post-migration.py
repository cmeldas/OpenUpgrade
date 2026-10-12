# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The noupdate template portal_share_template changed in 20.0 (t-out,
    # technical_usage 'mail_post_source' used to tag the source view of the
    # posted message). The data file only holds this template: reload it.
    openupgrade.load_data(env, "portal", "data/mail_templates.xml")
