# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

_TEMPLATES = [
    "mail_template_data_unregistered_users",
    "mail_template_user_signup_account_created",
    "portal_set_password_email",
    "set_password_email",
]


@openupgrade.migrate()
def migrate(env, version):
    # The noupdate mail templates now rely on the generic layout
    # (email_layout_xmlid + forced header/footer) instead of a hard-coded HTML
    # layout. The v19 body of set_password_email also calls
    # ir.config_parameter.get_param(), which no longer exists in v20 (only the
    # typed getters get_str/get_int/... remain): without this update, sending
    # an invitation to a new internal user fails to render.
    openupgrade.load_data(env, "auth_signup", "20.0.1.0/noupdate_changes.xml")
    # Drop the old translations of the body (including the copy of get_param in
    # them), so that the module's translation update right after this script
    # fills them again from the v20 .po files. Languages without a translation
    # fall back to en_US. Subjects are untouched (they did not change and may
    # be customized).
    openupgrade.delete_record_translations(
        env.cr, "auth_signup", _TEMPLATES, ["body_html"]
    )
