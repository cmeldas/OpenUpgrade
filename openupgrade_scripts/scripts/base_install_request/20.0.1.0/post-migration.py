# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    # The request e-mail is now rendered from the QWeb template
    # base_install_request.mail_installation_request_template (see
    # base.module.install.request.qweb_template_xmlid). The old mail.template is
    # noupdate, so the regular cleanup at the end of the update would keep it.
    openupgrade.delete_records_safely_by_xml_id(
        env, ["base_install_request.mail_template_base_install_request"]
    )
