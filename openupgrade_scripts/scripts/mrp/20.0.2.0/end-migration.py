# Copyright 2026 Matouš Čmelík
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from openupgradelib import openupgrade

# v19 had one subtype per manufacturing order state, v20 has a single one
_obsolete_subtypes = [
    "mrp.mrp_mo_in_confirmed",
    "mrp.mrp_mo_in_progress",
    "mrp.mrp_mo_in_to_close",
    "mrp.mrp_mo_in_done",
    "mrp.mrp_mo_in_cancelled",
]


def merge_mo_state_subtypes(env):
    """Point the messages and the followers of the obsolete state subtypes to
    mrp.mt_mo_state before deleting them, so the chatter history keeps its
    subtype. Done in end-migration, after mail rendered the tracking values."""
    target = env.ref("mrp.mt_mo_state", raise_if_not_found=False)
    if not target:
        return
    env.cr.execute(
        """
        SELECT res_id FROM ir_model_data
        WHERE model = 'mail.message.subtype' AND module = 'mrp'
            AND name IN %s AND res_id != %s
        """,
        (tuple(xmlid.split(".")[1] for xmlid in _obsolete_subtypes), target.id),
    )
    old_ids = tuple(row[0] for row in env.cr.fetchall())
    if not old_ids:
        return
    openupgrade.logged_query(
        env.cr,
        "UPDATE mail_message SET subtype_id = %s WHERE subtype_id IN %s",
        (target.id, old_ids),
    )
    openupgrade.logged_query(
        env.cr,
        "UPDATE mail_message_subtype SET parent_id = %s WHERE parent_id IN %s",
        (target.id, old_ids),
    )
    openupgrade.logged_query(
        env.cr,
        """
        INSERT INTO mail_followers_mail_message_subtype_rel
            (mail_followers_id, mail_message_subtype_id)
        SELECT DISTINCT rel.mail_followers_id, %s
        FROM mail_followers_mail_message_subtype_rel rel
        WHERE rel.mail_message_subtype_id IN %s
        ON CONFLICT DO NOTHING
        """,
        (target.id, old_ids),
    )
    env["mail.message"].invalidate_model(["subtype_id"])
    openupgrade.delete_records_safely_by_xml_id(env, _obsolete_subtypes)


@openupgrade.migrate()
def migrate(env, version):
    merge_mo_state_subtypes(env)
