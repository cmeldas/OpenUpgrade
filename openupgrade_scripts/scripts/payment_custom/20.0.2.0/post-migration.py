# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade
from psycopg2.extras import Json


def _redirect_form_view(env):
    """The view payment_custom.redirect_form is removed in favour of
    payment.generic_redirect_form. Custom providers of other modules (cash on
    delivery, pay on site...) point to it too and their data is noupdate: the
    foreign key (ondelete restrict) would block the removal of the view."""
    old_view = env.ref("payment_custom.redirect_form", raise_if_not_found=False)
    new_view = env.ref("payment.generic_redirect_form", raise_if_not_found=False)
    if not old_view or not new_view:
        return
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE payment_provider
           SET redirect_form_view_id = %s
         WHERE redirect_form_view_id = %s
        """,
        (new_view.id, old_view.id),
    )


def _wire_transfer_pending_msg(env):
    """v19 generated the pending message of the wire transfer provider with
    the bank accounts of the company (`_transfer_ensure_pending_msg_is_set`).
    v20 shows the bank account with a template of its own
    (payment_custom.custom_payment_transfer_details), so the generated message
    would display the (outdated) details twice. Reset it only if it is the
    generated one."""
    openupgrade.logged_query(
        env.cr,
        """
        UPDATE payment_provider
           SET pending_msg = %s
         WHERE code = 'custom'
           AND custom_mode = 'wire_transfer'
           AND pending_msg->>'en_US' LIKE %s
        """,
        (
            Json({"en_US": "<p>Please find the payment details below.</p>"}),
            "<h5>Please use the following transfer details</h5>%",
        ),
    )


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.load_data(env, "payment_custom", "20.0.2.0/noupdate_changes.xml")
    _redirect_form_view(env)
    _wire_transfer_pending_msg(env)
    env["payment.provider"].invalidate_model(["redirect_form_view_id", "pending_msg"])
